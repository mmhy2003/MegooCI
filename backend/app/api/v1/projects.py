import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.access import ALL_PROJECTS, accessible_project_ids
from app.core.deps import check_scoped_permission, get_current_active_user, require_permission
from app.database import get_db
from app.models.git_integration import ProjectRepository, WebhookDelivery
from app.models.pipeline import Pipeline
from app.models.project import Project
from app.models.secret import EnvVar, Secret
from app.models.user import User
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectUpdate,
)

router = APIRouter()


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return re.sub(r"-+", "-", slug).strip("-")


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> ProjectListResponse:
    pids = accessible_project_ids(_current_user, "projects.read")
    if pids is not ALL_PROJECTS and not pids:
        return ProjectListResponse(items=[], total=0)
    query = select(Project).order_by(Project.created_at.desc())
    count_query = select(func.count()).select_from(Project)
    if pids is not ALL_PROJECTS:
        query = query.where(Project.id.in_(pids))
        count_query = count_query.where(Project.id.in_(pids))
    total = await db.scalar(count_query) or 0
    result = await db.execute(query.offset(skip).limit(limit))
    items = [ProjectResponse.model_validate(p) for p in result.scalars().all()]
    return ProjectListResponse(items=items, total=total)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("projects.manage")),
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
    current_user: User = Depends(get_current_active_user),
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    check_scoped_permission(current_user, "projects.read", "project", project_id)
    return project


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> list[dict]:
    from app.models.role import Role, UserRole
    from app.models.user import User as UserModel
    rows = await db.execute(
        select(UserRole.id, UserModel.id, UserModel.email, UserModel.name, Role.name)
        .join(UserModel, UserRole.user_id == UserModel.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.scope_type == "project", UserRole.scope_id == project_id)
        .order_by(UserModel.email)
    )
    return [
        {"user_role_id": ur_id, "user_id": uid, "email": email, "name": name, "role_name": rn}
        for ur_id, uid, email, name, rn in rows.all()
    ]


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )
    check_scoped_permission(current_user, "projects.manage", "project", project_id)

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
    current_user: User = Depends(get_current_active_user),
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
    check_scoped_permission(current_user, "projects.manage", "project", project_id)
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
        # Cascade path.  Order matters: delete deepest children first so FK
        # constraints are satisfied at each step.
        #
        # Dependency graph:
        #   Pipeline ← Build ← Stage ← Step ← LogChunk
        #   Pipeline ← Trigger
        #   Pipeline ← WebhookEndpoint
        #   Pipeline.project_repository_id → ProjectRepository
        #   ProjectRepository ← WebhookDelivery
        #   Build  ← Artifact
        #   Build  ← NotificationDelivery (ON DELETE CASCADE)
        #
        # So pipelines MUST be deleted before repos, and all pipeline
        # children (builds, triggers, etc.) before pipelines.

        from app.models.artifact import Artifact
        from app.models.build import Build, LogChunk, Stage, Step
        from app.models.trigger import Trigger, WebhookEndpoint

        # 1) Collect pipeline IDs scoped to this project.
        pipe_id_rows = await db.execute(
            select(Pipeline.id).where(Pipeline.project_id == project_id)
        )
        pipe_ids = [row[0] for row in pipe_id_rows.all()]

        if pipe_ids:
            # 2) Collect build IDs for these pipelines.
            build_id_rows = await db.execute(
                select(Build.id).where(Build.pipeline_id.in_(pipe_ids))
            )
            build_ids = [row[0] for row in build_id_rows.all()]

            if build_ids:
                # Delete artifacts for these builds.
                await db.execute(
                    sa_delete(Artifact).where(Artifact.build_id.in_(build_ids))
                )
                # Collect stage IDs.
                stage_id_rows = await db.execute(
                    select(Stage.id).where(Stage.build_id.in_(build_ids))
                )
                stage_ids = [row[0] for row in stage_id_rows.all()]
                if stage_ids:
                    # Collect step IDs.
                    step_id_rows = await db.execute(
                        select(Step.id).where(Step.stage_id.in_(stage_ids))
                    )
                    step_ids = [row[0] for row in step_id_rows.all()]
                    if step_ids:
                        await db.execute(
                            sa_delete(LogChunk).where(LogChunk.step_id.in_(step_ids))
                        )
                    await db.execute(
                        sa_delete(Step).where(Step.stage_id.in_(stage_ids))
                    )
                await db.execute(
                    sa_delete(Stage).where(Stage.build_id.in_(build_ids))
                )
                await db.execute(
                    sa_delete(Build).where(Build.id.in_(build_ids))
                )

            # Delete pipeline-level children: triggers + webhook endpoints.
            await db.execute(
                sa_delete(Trigger).where(Trigger.pipeline_id.in_(pipe_ids))
            )
            await db.execute(
                sa_delete(WebhookEndpoint).where(
                    WebhookEndpoint.pipeline_id.in_(pipe_ids)
                )
            )

            # Now safe to delete pipelines (unblocks repo FK).
            await db.execute(
                sa_delete(Pipeline).where(Pipeline.project_id == project_id)
            )

        # Repos can now be deleted safely since no pipelines reference them.
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

    from app.models.role import UserRole
    await db.execute(
        sa_delete(UserRole).where(
            UserRole.scope_type == "project", UserRole.scope_id == project_id
        )
    )
    await db.delete(project)
    await db.commit()

    from app.services.search import remove_project
    await remove_project(str(project_id))
