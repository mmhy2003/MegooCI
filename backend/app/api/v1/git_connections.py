"""Admin-scoped Git provider connections (PRD §6.16 / F-16.1).

All endpoints require an active admin user. Plaintext credentials are never
returned; only a 4-character `credential_hint` is surfaced for UI display.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_active_user, get_current_admin_user
from app.core.security import credential_hint, decrypt_secret, encrypt_secret
from app.database import get_db
from app.models.git_integration import GitProviderConnection, ProjectRepository
from app.models.user import User
from app.schemas.git_integration import (
    ConnectionCreate,
    ConnectionResponse,
    ConnectionTestResult,
    ConnectionUpdate,
    ProviderRepositoryInfo,
    ProviderRepositoryList,
)
from app.services.git_providers import ValidationResult, get_adapter

router = APIRouter()


async def _run_test(connection: GitProviderConnection) -> ValidationResult:
    """Run the provider-specific credential test with the currently stored
    (encrypted) credential. Caller is responsible for persisting the result.
    """
    settings = get_settings()
    try:
        adapter = get_adapter(connection.provider_type)
    except ValueError as exc:
        return ValidationResult(ok=False, status="failed", detail=str(exc))

    try:
        token = decrypt_secret(connection.encrypted_credential, settings.MEGOOCI_SECRET_KEY)
    except Exception as exc:
        return ValidationResult(
            ok=False,
            status="failed",
            detail=f"Unable to decrypt stored credential: {exc}",
        )

    return await adapter.test_credential(connection.base_url, token)


def _apply_validation_result(
    connection: GitProviderConnection, result: ValidationResult
) -> None:
    connection.validation_status = "ok" if result.ok else "failed"
    connection.last_validated_at = datetime.now(timezone.utc)
    connection.validation_error = None if result.ok else result.detail[:2000]


@router.get("/", response_model=list[ConnectionResponse])
async def list_connections(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> list[GitProviderConnection]:
    result = await db.execute(
        select(GitProviderConnection).order_by(GitProviderConnection.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/",
    response_model=ConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_connection(
    body: ConnectionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
) -> GitProviderConnection:
    if body.auth_mode != "pat":
        # OAuth is Phase 2; model already has the columns so future work
        # doesn't need a migration.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="OAuth connections are not yet supported. Use auth_mode='pat'.",
        )

    try:
        get_adapter(body.provider_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    settings = get_settings()
    connection = GitProviderConnection(
        name=body.name,
        provider_type=body.provider_type,
        base_url=body.base_url,
        auth_mode=body.auth_mode,
        encrypted_credential=encrypt_secret(body.credential, settings.MEGOOCI_SECRET_KEY),
        credential_hint=credential_hint(body.credential),
        validation_status="unknown",
        created_by=current_user.id,
    )
    db.add(connection)
    # Flush to populate PK and check unique name constraint before running
    # the network call.
    try:
        await db.flush()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not create connection: {exc}",
        )

    # Run an immediate validation so the UI shows ok/failed straight away.
    result = await _run_test(connection)
    _apply_validation_result(connection, result)

    await db.commit()
    await db.refresh(connection)
    return connection


@router.get("/{connection_id}", response_model=ConnectionResponse)
async def get_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> GitProviderConnection:
    connection = await db.get(GitProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found"
        )
    return connection


@router.put("/{connection_id}", response_model=ConnectionResponse)
async def update_connection(
    connection_id: uuid.UUID,
    body: ConnectionUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> GitProviderConnection:
    connection = await db.get(GitProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found"
        )

    if body.name is not None:
        connection.name = body.name
    if body.base_url is not None:
        connection.base_url = body.base_url

    credential_rotated = False
    if body.credential:
        settings = get_settings()
        connection.encrypted_credential = encrypt_secret(
            body.credential, settings.MEGOOCI_SECRET_KEY
        )
        connection.credential_hint = credential_hint(body.credential)
        credential_rotated = True
        # Invalidate any stale validation result until the next /test.
        connection.validation_status = "unknown"
        connection.last_validated_at = None
        connection.validation_error = None

    await db.flush()
    if credential_rotated:
        result = await _run_test(connection)
        _apply_validation_result(connection, result)

    await db.commit()
    await db.refresh(connection)
    return connection


@router.delete("/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> None:
    connection = await db.get(GitProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found"
        )

    # Refuse if any ProjectRepository references this connection (F-16.13).
    linked = await db.scalar(
        select(ProjectRepository.id).where(
            ProjectRepository.connection_id == connection_id
        )
    )
    if linked is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete connection: one or more project repositories "
                "still reference it. Unlink them first."
            ),
        )

    await db.delete(connection)
    await db.commit()


@router.post("/{connection_id}/test", response_model=ConnectionTestResult)
async def test_connection(
    connection_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_admin_user),
) -> ConnectionTestResult:
    connection = await db.get(GitProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found"
        )

    result = await _run_test(connection)
    _apply_validation_result(connection, result)
    await db.commit()

    return ConnectionTestResult(
        ok=result.ok,
        status=result.status,
        detail=result.detail,
        http_status=result.http_status,
        latency_ms=result.latency_ms,
    )


@router.get(
    "/{connection_id}/repositories", response_model=ProviderRepositoryList
)
async def list_provider_repositories(
    connection_id: uuid.UUID,
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> ProviderRepositoryList:
    """List repositories visible to this connection's PAT.

    Open to any authenticated user (not just admins) so project owners can
    browse and pick a repo when linking it to their project. The credential
    itself is never returned - only the resulting repository list.
    """
    connection = await db.get(GitProviderConnection, connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found"
        )

    try:
        adapter = get_adapter(connection.provider_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        )

    settings = get_settings()
    try:
        token = decrypt_secret(
            connection.encrypted_credential, settings.MEGOOCI_SECRET_KEY
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unable to decrypt stored credential: {exc}",
        )

    result = await adapter.list_repositories(connection.base_url, token, limit)
    return ProviderRepositoryList(
        ok=result.ok,
        status=result.status,
        detail=result.detail,
        repositories=[
            ProviderRepositoryInfo(
                full_name=r.full_name,
                clone_url=r.clone_url,
                default_branch=r.default_branch,
                private=r.private,
                description=r.description,
                html_url=r.html_url,
                updated_at=r.updated_at,
            )
            for r in result.repositories
        ],
    )
