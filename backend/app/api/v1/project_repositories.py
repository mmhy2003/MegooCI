"""Per-project Git repository links (PRD §6.16 / F-16.5 - F-16.11).

Each linked repository inherits its connection from an admin-created
`GitProviderConnection` and exposes a manual-paste webhook URL + secret that
the user installs on the provider. Access is limited to authenticated users;
project-level RBAC scoping is tracked separately (see PRD §6.15 / F-7.8).
"""

from __future__ import annotations

import uuid
from urllib.parse import urljoin

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_active_user
from app.core.security import (
    encrypt_webhook_secret,
    generate_webhook_secret,
    generate_webhook_slug,
)
from app.database import get_db
from app.models.git_integration import (
    GitProviderConnection,
    ProjectRepository,
    WebhookDelivery,
)
from app.models.project import Project
from app.models.user import User
from app.schemas.git_integration import (
    ProjectRepositoryCreate,
    ProjectRepositoryResponse,
    ProjectRepositoryUpdate,
    ProjectRepositoryWithSecretResponse,
    WebhookDeliveryResponse,
)

router = APIRouter()


def _build_webhook_url(slug: str) -> str:
    settings = get_settings()
    base = (settings.MEGOOCI_PUBLIC_URL or "").rstrip("/") + "/"
    # Using urljoin to respect whatever base path MEGOOCI_PUBLIC_URL carries.
    return urljoin(base, f"api/v1/webhooks/git/{slug}")


async def _get_project_or_404(db: AsyncSession, project_id: uuid.UUID) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return project


async def _get_repo_or_404(
    db: AsyncSession, project_id: uuid.UUID, repo_id: uuid.UUID
) -> ProjectRepository:
    repo = await db.get(ProjectRepository, repo_id)
    if repo is None or repo.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Linked repository not found",
        )
    return repo


def _with_secret_response(
    repo: ProjectRepository, plaintext_secret: str
) -> ProjectRepositoryWithSecretResponse:
    return ProjectRepositoryWithSecretResponse(
        id=repo.id,
        project_id=repo.project_id,
        connection_id=repo.connection_id,
        repo_url=repo.repo_url,
        default_branch=repo.default_branch,
        display_name=repo.display_name,
        webhook_slug=repo.webhook_slug,
        last_event_at=repo.last_event_at,
        last_event_status=repo.last_event_status,
        created_by=repo.created_by,
        created_at=repo.created_at,
        updated_at=repo.updated_at,
        webhook_secret=plaintext_secret,
        webhook_url=_build_webhook_url(repo.webhook_slug),
    )


@router.get("/", response_model=list[ProjectRepositoryResponse])
async def list_repositories(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[ProjectRepository]:
    await _get_project_or_404(db, project_id)
    result = await db.execute(
        select(ProjectRepository)
        .where(ProjectRepository.project_id == project_id)
        .order_by(ProjectRepository.created_at.desc())
    )
    return list(result.scalars().all())


@router.post(
    "/",
    response_model=ProjectRepositoryWithSecretResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_repository(
    project_id: uuid.UUID,
    body: ProjectRepositoryCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> ProjectRepositoryWithSecretResponse:
    await _get_project_or_404(db, project_id)
    connection = await db.get(GitProviderConnection, body.connection_id)
    if connection is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Connection not found"
        )

    settings = get_settings()
    slug = generate_webhook_slug()
    plaintext_secret = generate_webhook_secret()

    repo = ProjectRepository(
        project_id=project_id,
        connection_id=connection.id,
        repo_url=body.repo_url,
        default_branch=body.default_branch,
        display_name=body.display_name,
        webhook_slug=slug,
        webhook_secret_hash=encrypt_webhook_secret(
            plaintext_secret, settings.MEGOOCI_SECRET_KEY
        ),
        created_by=current_user.id,
    )
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return _with_secret_response(repo, plaintext_secret)


@router.put("/{repo_id}", response_model=ProjectRepositoryResponse)
async def update_repository(
    project_id: uuid.UUID,
    repo_id: uuid.UUID,
    body: ProjectRepositoryUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> ProjectRepository:
    repo = await _get_repo_or_404(db, project_id, repo_id)
    if body.default_branch is not None:
        repo.default_branch = body.default_branch
    if body.display_name is not None:
        repo.display_name = body.display_name
    await db.commit()
    await db.refresh(repo)
    return repo


@router.delete("/{repo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_repository(
    project_id: uuid.UUID,
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> None:
    repo = await _get_repo_or_404(db, project_id, repo_id)
    # Cascade handles WebhookDelivery rows (models.relationship cascade).
    await db.delete(repo)
    await db.commit()


@router.post(
    "/{repo_id}/rotate-secret",
    response_model=ProjectRepositoryWithSecretResponse,
)
async def rotate_webhook_secret(
    project_id: uuid.UUID,
    repo_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> ProjectRepositoryWithSecretResponse:
    repo = await _get_repo_or_404(db, project_id, repo_id)
    settings = get_settings()
    plaintext_secret = generate_webhook_secret()
    repo.webhook_secret_hash = encrypt_webhook_secret(
        plaintext_secret, settings.MEGOOCI_SECRET_KEY
    )
    await db.commit()
    await db.refresh(repo)
    return _with_secret_response(repo, plaintext_secret)


@router.get(
    "/{repo_id}/deliveries", response_model=list[WebhookDeliveryResponse]
)
async def list_deliveries(
    project_id: uuid.UUID,
    repo_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[WebhookDelivery]:
    await _get_repo_or_404(db, project_id, repo_id)
    result = await db.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.project_repository_id == repo_id)
        .order_by(WebhookDelivery.received_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())
