"""Registry management API — ``/api/v1/registry/`` endpoints.

These endpoints power the frontend UI for browsing repositories, images,
tags, deploy tokens, and registry events. They are separate from the
``/v2/`` OCI endpoints which serve Docker/OCI clients.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.deps import require_permission
from app.core.security import hash_password, credential_hint
from app.database import get_db
from app.models.project import Project
from app.models.registry import (
    ContainerImage,
    ContainerRepository,
    ContainerTag,
    RegistryDeployToken,
    RegistryEvent,
)
from app.models.user import User
from app.schemas.registry import (
    ContainerImageDetailResponse,
    ContainerImageResponse,
    ContainerRepositoryResponse,
    ContainerRepositoryUpdate,
    ContainerTagResponse,
    DeployTokenCreate,
    DeployTokenCreatedResponse,
    DeployTokenResponse,
    RegistryEventResponse,
    RegistryOverview,
)
from app.services import registry_storage as storage

router = APIRouter()


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@router.get("/overview", response_model=RegistryOverview)
async def registry_overview(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> RegistryOverview:
    total_repos = (await db.execute(
        select(func.count(ContainerRepository.id))
    )).scalar_one()
    total_images = (await db.execute(
        select(func.count(ContainerImage.id))
    )).scalar_one()
    total_tags = (await db.execute(
        select(func.count(ContainerTag.id))
    )).scalar_one()
    total_size = (await db.execute(
        select(func.coalesce(func.sum(ContainerImage.size_bytes), 0))
    )).scalar_one()

    return RegistryOverview(
        total_repositories=total_repos,
        total_images=total_images,
        total_tags=total_tags,
        total_size_bytes=total_size,
    )


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------

@router.get("/repositories", response_model=list[ContainerRepositoryResponse])
async def list_repositories(
    project_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> list[ContainerRepository]:
    q = select(ContainerRepository).order_by(ContainerRepository.created_at.desc())
    if project_id:
        q = q.where(ContainerRepository.project_id == project_id)
    q = q.offset(skip).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/repositories/{repo_id}", response_model=ContainerRepositoryResponse)
async def get_repository(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> ContainerRepository:
    result = await db.execute(
        select(ContainerRepository).where(ContainerRepository.id == repo_id)
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.put("/repositories/{repo_id}", response_model=ContainerRepositoryResponse)
async def update_repository(
    repo_id: uuid.UUID,
    body: ContainerRepositoryUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.manage")),
) -> ContainerRepository:
    result = await db.execute(
        select(ContainerRepository).where(ContainerRepository.id == repo_id)
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(repo, field, value)

    await db.flush()
    await db.refresh(repo)
    return repo


@router.delete("/repositories/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.manage")),
) -> None:
    result = await db.execute(
        select(ContainerRepository).where(ContainerRepository.id == repo_id)
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404)
    await db.delete(repo)


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

@router.get("/repositories/{repo_id}/images", response_model=list[ContainerImageResponse])
async def list_images(
    repo_id: uuid.UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> list[ContainerImage]:
    q = (
        select(ContainerImage)
        .where(ContainerImage.repository_id == repo_id)
        .order_by(ContainerImage.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


@router.get("/images/{image_id}", response_model=ContainerImageDetailResponse)
async def get_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> ContainerImage:
    result = await db.execute(
        select(ContainerImage)
        .options(selectinload(ContainerImage.tags))
        .where(ContainerImage.id == image_id)
    )
    image = result.scalar_one_or_none()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return image


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

@router.get("/repositories/{repo_id}/tags", response_model=list[ContainerTagResponse])
async def list_tags_api(
    repo_id: uuid.UUID,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> list[ContainerTag]:
    q = (
        select(ContainerTag)
        .where(ContainerTag.repository_id == repo_id)
        .order_by(ContainerTag.name)
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(q)
    return list(result.scalars().all())


@router.delete("/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.manage")),
) -> None:
    result = await db.execute(
        select(ContainerTag).where(ContainerTag.id == tag_id)
    )
    tag = result.scalar_one_or_none()
    if tag is None:
        raise HTTPException(status_code=404)
    await db.delete(tag)


# ---------------------------------------------------------------------------
# Deploy Tokens
# ---------------------------------------------------------------------------

@router.get("/deploy-tokens", response_model=list[DeployTokenResponse])
async def list_deploy_tokens(
    project_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.manage")),
) -> list[RegistryDeployToken]:
    q = select(RegistryDeployToken).order_by(RegistryDeployToken.created_at.desc())
    if project_id:
        q = q.where(RegistryDeployToken.project_id == project_id)
    result = await db.execute(q)
    return list(result.scalars().all())


@router.post(
    "/deploy-tokens",
    response_model=DeployTokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_deploy_token(
    body: DeployTokenCreate,
    project_id: uuid.UUID = Query(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("registry.manage")),
) -> dict:
    project = (await db.execute(
        select(Project).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if body.scope not in ("pull", "push"):
        raise HTTPException(status_code=400, detail="Scope must be 'pull' or 'push'")

    raw_token = f"megci_reg_{secrets.token_urlsafe(32)}"
    hashed = hash_password(raw_token)

    expires_at = None
    if body.expires_in_days:
        expires_at = datetime.now(timezone.utc) + timedelta(days=body.expires_in_days)

    dt = RegistryDeployToken(
        project_id=project_id,
        name=body.name,
        token_hash=hashed,
        token_hint=credential_hint(raw_token),
        scope=body.scope,
        expires_at=expires_at,
        created_by=current_user.id,
    )
    db.add(dt)
    await db.flush()
    await db.refresh(dt)

    resp = DeployTokenCreatedResponse.model_validate(dt)
    resp.token = raw_token
    return resp.model_dump()


@router.delete("/deploy-tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_deploy_token(
    token_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.manage")),
) -> None:
    result = await db.execute(
        select(RegistryDeployToken).where(RegistryDeployToken.id == token_id)
    )
    dt = result.scalar_one_or_none()
    if dt is None:
        raise HTTPException(status_code=404)
    dt.is_active = False
    await db.flush()


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@router.get("/events", response_model=list[RegistryEventResponse])
async def list_events(
    repository_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("registry.read")),
) -> list[RegistryEvent]:
    q = select(RegistryEvent).order_by(RegistryEvent.created_at.desc())
    if repository_id:
        q = q.where(RegistryEvent.repository_id == repository_id)
    q = q.offset(skip).limit(limit)
    result = await db.execute(q)
    return list(result.scalars().all())
