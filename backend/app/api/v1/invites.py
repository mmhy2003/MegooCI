import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.deps import require_permission
from app.core.email import send_invite_email
from app.core.security import hash_password
from app.database import get_db
from app.models.invite import Invite
from app.models.role import Role, UserRole
from app.models.user import User
from app.schemas.auth import TokenResponse
from app.schemas.invites import (
    AcceptInviteRequest,
    InviteCreate,
    InviteCreatedResponse,
    InviteResponse,
)

router = APIRouter()


def _generate_invite_token() -> str:
    return secrets.token_urlsafe(48)


def _hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


@router.get("/", response_model=list[InviteResponse])
async def list_invites(
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("invites.manage")),
) -> list[dict]:
    query = (
        select(Invite)
        .options(selectinload(Invite.role), selectinload(Invite.creator))
        .order_by(Invite.created_at.desc())
    )
    if status_filter:
        query = query.where(Invite.status == status_filter)
    result = await db.execute(query)
    invites = result.scalars().all()
    return [
        {
            **{c.key: getattr(inv, c.key) for c in Invite.__table__.columns},
            "role_name": inv.role.name if inv.role else None,
            "creator_name": inv.creator.name if inv.creator else None,
        }
        for inv in invites
    ]


@router.post("/", response_model=InviteCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_invite(
    body: InviteCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("invites.manage")),
) -> dict:
    settings = get_settings()

    existing_user = await db.execute(select(User).where(User.email == body.email))
    if existing_user.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    pending = await db.execute(
        select(Invite).where(Invite.email == body.email, Invite.status == "pending")
    )
    if pending.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A pending invitation already exists for this email",
        )

    role = await db.execute(select(Role).where(Role.id == body.role_id))
    if role.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found"
        )

    token = _generate_invite_token()
    invite = Invite(
        email=body.email,
        role_id=body.role_id,
        token_hash=_hash_token(token),
        expires_at=datetime.now(timezone.utc)
        + timedelta(hours=settings.MEGOOCI_INVITE_EXPIRY_HOURS),
        created_by=current_user.id,
    )
    db.add(invite)
    await db.flush()
    await db.refresh(invite, attribute_names=["role", "creator"])

    frontend_base = settings.MEGOOCI_PUBLIC_URL.rstrip("/")
    invite_link = f"{frontend_base}/invite/accept?token={token}"

    await send_invite_email(
        db=db,
        to_email=body.email,
        invite_link=invite_link,
        inviter_name=current_user.name,
    )

    return {
        **{c.key: getattr(invite, c.key) for c in Invite.__table__.columns},
        "role_name": invite.role.name if invite.role else None,
        "creator_name": invite.creator.name if invite.creator else None,
        "invite_link": invite_link,
    }


@router.post("/accept", response_model=TokenResponse)
async def accept_invite(
    body: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.core.security import create_access_token, create_refresh_token

    token_hash = _hash_token(body.token)
    result = await db.execute(
        select(Invite)
        .options(selectinload(Invite.role))
        .where(Invite.token_hash == token_hash, Invite.status == "pending")
    )
    invite = result.scalar_one_or_none()

    if invite is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid or expired invitation",
        )

    if invite.expires_at < datetime.now(timezone.utc):
        invite.status = "expired"
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail="This invitation has expired",
        )

    existing = await db.execute(select(User).where(User.email == invite.email))
    if existing.scalar_one_or_none() is not None:
        invite.status = "accepted"
        invite.accepted_at = datetime.now(timezone.utc)
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists",
        )

    is_admin_role = invite.role and invite.role.name == "admin"
    user = User(
        email=invite.email,
        name=body.name,
        hashed_password=hash_password(body.password),
        is_admin=is_admin_role,
        is_active=True,
    )
    db.add(user)
    await db.flush()

    if is_admin_role:
        db.add(UserRole(user_id=user.id, role_id=invite.role_id, scope_type="global"))
    # Non-admin invitees start with no role; project assignment follows.

    invite.status = "accepted"
    invite.accepted_at = datetime.now(timezone.utc)
    await db.flush()

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }



@router.delete("/cleanup", status_code=status.HTTP_200_OK)
async def cleanup_invites(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("invites.manage")),
) -> dict:
    """Delete all non-pending invitations (accepted, revoked, expired)."""
    from sqlalchemy import delete as sa_delete

    result = await db.execute(
        sa_delete(Invite).where(Invite.status.in_(["accepted", "revoked", "expired"]))
    )
    return {"deleted": result.rowcount}


@router.delete("/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(
    invite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("invites.manage")),
) -> None:
    result = await db.execute(select(Invite).where(Invite.id == invite_id))
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot revoke an invite with status '{invite.status}'",
        )
    invite.status = "revoked"


@router.post("/{invite_id}/resend", response_model=InviteResponse)
async def resend_invite(
    invite_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("invites.manage")),
) -> dict:
    settings = get_settings()
    result = await db.execute(
        select(Invite)
        .options(selectinload(Invite.role), selectinload(Invite.creator))
        .where(Invite.id == invite_id)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only pending invites can be resent",
        )

    token = _generate_invite_token()
    invite.token_hash = _hash_token(token)
    invite.expires_at = datetime.now(timezone.utc) + timedelta(
        hours=settings.MEGOOCI_INVITE_EXPIRY_HOURS
    )
    await db.flush()

    frontend_base = settings.MEGOOCI_PUBLIC_URL.rstrip("/")
    invite_link = f"{frontend_base}/invite/accept?token={token}"
    await send_invite_email(
        db=db,
        to_email=invite.email,
        invite_link=invite_link,
        inviter_name=current_user.name,
    )

    return {
        **{c.key: getattr(invite, c.key) for c in Invite.__table__.columns},
        "role_name": invite.role.name if invite.role else None,
        "creator_name": invite.creator.name if invite.creator else None,
    }
