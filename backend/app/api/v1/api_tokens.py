"""Personal Access Token (PAT) management endpoints.

Users can create, list, and revoke their own API tokens.
"""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.security import generate_pat, hash_pat, pat_hint
from app.database import get_db
from app.models.api_token import ApiToken
from app.models.user import User
from app.core.token_scopes import (
    FULL_ACCESS_KEY,
    TOKEN_SCOPES,
    resolve_scope,
    scope_catalog,
)

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────


class ScopeInfo(BaseModel):
    key: str
    label: str


class ScopeCatalogItem(BaseModel):
    key: str
    label: str
    description: str


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: int | None = Field(
        None, ge=1, le=365, description="Days until expiry (null = never)"
    )
    scope: str | None = Field(
        None,
        description="Scope key from GET /tokens/scopes; null or 'full_access' = full access",
    )


class TokenResponse(BaseModel):
    id: uuid.UUID
    name: str
    token_hint: str
    scopes: list[str] | None
    scope: ScopeInfo
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None
    created_at: datetime


class TokenCreatedResponse(TokenResponse):
    """Returned only at creation time — includes the raw token value."""
    token: str


# ── Endpoints ────────────────────────────────────────────────────────────


def _token_response(t: ApiToken) -> TokenResponse:
    return TokenResponse(
        id=t.id,
        name=t.name,
        token_hint=t.token_hint,
        scopes=t.scopes,
        scope=ScopeInfo(**resolve_scope(t.scopes)),
        expires_at=t.expires_at,
        is_active=t.is_active,
        last_used_at=t.last_used_at,
        created_at=t.created_at,
    )


@router.get("/tokens", response_model=list[TokenResponse])
async def list_tokens(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TokenResponse]:
    """List all API tokens for the current user."""
    result = await db.execute(
        select(ApiToken)
        .where(ApiToken.user_id == current_user.id)
        .order_by(ApiToken.created_at.desc())
    )
    rows = result.scalars().all()
    return [_token_response(t) for t in rows]


@router.get("/tokens/scopes", response_model=list[ScopeCatalogItem])
async def list_scopes(
    current_user: User = Depends(get_current_user),
) -> list[ScopeCatalogItem]:
    """List the functional scopes available when creating a token."""
    return [ScopeCatalogItem(**item) for item in scope_catalog()]


@router.post(
    "/tokens",
    response_model=TokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    body: CreateTokenRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> TokenCreatedResponse:
    """Create a new PAT.  The raw token is returned **once** — store it safely."""
    # Resolve & validate the requested scope.
    if body.scope is None or body.scope == FULL_ACCESS_KEY:
        stored_scopes: list[str] | None = None
    elif body.scope in TOKEN_SCOPES:
        stored_scopes = [body.scope]
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown scope '{body.scope}'",
        )

    raw_token = generate_pat()

    expires_at = None
    if body.expires_in_days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    api_token = ApiToken(
        user_id=current_user.id,
        name=body.name,
        token_hash=hash_pat(raw_token),
        token_hint=pat_hint(raw_token),
        scopes=stored_scopes,
        expires_at=expires_at,
    )
    db.add(api_token)
    await db.commit()
    await db.refresh(api_token)

    return TokenCreatedResponse(
        **_token_response(api_token).model_dump(),
        token=raw_token,
    )


@router.delete(
    "/tokens/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Revoke (deactivate) a PAT.  The token is kept for audit but marked inactive."""
    result = await db.execute(
        select(ApiToken).where(
            ApiToken.id == token_id,
            ApiToken.user_id == current_user.id,
        )
    )
    api_token = result.scalar_one_or_none()
    if api_token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Token not found"
        )

    api_token.is_active = False
    await db.commit()
