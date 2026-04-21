import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.database import get_db
from app.models.git_integration import ProjectRepository, WebhookDelivery
from app.models.pipeline import Pipeline
from app.models.project import Project
from app.models.secret import EnvVar, Secret
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter()


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Project]:
    result = await db.execute(
        select(Project).order_by(Project.created_at.desc()).offset(skip).limit(limit)
    )
    return list(result.scalars().all())


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Project:
    slug = _slugify(body.name)

    existing = await db.execute(select(Project).where(Project.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with slug '{slug}' already exists",
        )

    if body.parent_id is not None:
        parent = await db.get(Project, body.parent_id)
        if parent is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Parent project not found",
            )

    project = Project(
        name=body.name,
        slug=slug,
        description=body.description,
        parent_id=body.parent_id,
        created_by=current_user.id,
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    from app.services.search import index_project
    await index_project(project)

    return project


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    update_data = body.model_dump(exclude_unset=True)
    if "name" in update_data:
        new_slug = _slugify(update_data["name"])
        existing = await db.execute(
            select(Project).where(Project.slug == new_slug, Project.id != project_id)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Project with slug '{new_slug}' already exists",
            )
        project.slug = new_slug

    for field, value in update_data.items():
        setattr(project, field, value)

    await db.commit()
    await db.refresh(project)

    from app.services.search import index_project
    await index_project(project)

    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    force: bool = Query(
        False,
        description=(
            "When true, cascade-delete every pipeline, linked repository, "
            "webhook delivery, secret, and environment variable scoped to "
            "this project. Child projects still block the delete."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> None:
    """Delete a project.

    By default the endpoint refuses (409) if anything still references the
    project -- child projects, pipelines, linked repositories, or secrets /
    env vars scoped to it. The response body lists exactly what's in the way
    so the UI can tell the user what they need to clean up first.

    Pass ``?force=true`` to cascade-delete pipelines / repos / deliveries /
    secrets / env vars belonging to the project. Child projects always block
    the delete because their own data would become orphaned.
    """
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    # Child projects block the delete regardless of `force` -- re-parenting
    # their state is a user decision we don't want to automate.
    children_count = await db.scalar(
        select(func.count())
        .select_from(Project)
        .where(Project.parent_id == project_id)
    ) or 0
    if children_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Cannot delete project: it has {children_count} child "
                f"project(s). Delete or re-parent them first."
            ),
        )

    # Tally dependent rows so we can either report them to the user (default)
    # or cascade (force=true). Secrets + env vars reference projects via
    # `scope_id` rather than an FK, so we filter on both scope_type + scope_id.
    pipeline_count = await db.scalar(
        select(func.count())
        .select_from(Pipeline)
        .where(Pipeline.project_id == project_id)
    ) or 0
    repo_count = await db.scalar(
        select(func.count())
        .select_from(ProjectRepository)
        .where(ProjectRepository.project_id == project_id)
    ) or 0
    secret_count = await db.scalar(
        select(func.count())
        .select_from(Secret)
        .where(Secret.scope_type == "project", Secret.scope_id == project_id)
    ) or 0
    env_count = await db.scalar(
        select(func.count())
        .select_from(EnvVar)
        .where(EnvVar.scope_type == "project", EnvVar.scope_id == project_id)
    ) or 0

    total_dependents = pipeline_count + repo_count + secret_count + env_count

    if total_dependents > 0 and not force:
        parts: list[str] = []
        if pipeline_count:
            parts.append(f"{pipeline_count} pipeline(s)")
        if repo_count:
            parts.append(f"{repo_count} linked repository(ies)")
        if secret_count:
            parts.append(f"{secret_count} secret(s)")
        if env_count:
            parts.append(f"{env_count} environment variable(s)")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete project: it still has "
                + ", ".join(parts)
                + ". Remove them first, or retry with ?force=true to "
                + "cascade-delete everything in this project."
            ),
        )

    if total_dependents > 0:
        # Cascade path. Order matters: delete deepest children first so FK
        # constraints are satisfied at each step. Webhook deliveries belong
        # to repositories, repositories belong to the project, pipelines are
        # independent of repos, and secrets/envs are scope-only refs.
        repo_ids_rows = await db.execute(
            select(ProjectRepository.id).where(
                ProjectRepository.project_id == project_id
            )
        )
        repo_ids = [row[0] for row in repo_ids_rows.all()]
        if repo_ids:
            await db.execute(
                sa_delete(WebhookDelivery).where(
                    WebhookDelivery.project_repository_id.in_(repo_ids)
                )
            )
            await db.execute(
                sa_delete(ProjectRepository).where(
                    ProjectRepository.id.in_(repo_ids)
                )
            )

        if pipeline_count:
            await db.execute(
                sa_delete(Pipeline).where(Pipeline.project_id == project_id)
            )
        if secret_count:
            await db.execute(
                sa_delete(Secret).where(
                    Secret.scope_type == "project",
                    Secret.scope_id == project_id,
                )
            )
        if env_count:
            await db.execute(
                sa_delete(EnvVar).where(
                    EnvVar.scope_type == "project",
                    EnvVar.scope_id == project_id,
                )
            )

    await db.delete(project)
    await db.commit()

    from app.services.search import remove_project
    await remove_project(str(project_id))
