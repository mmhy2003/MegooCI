import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.database import get_db
from app.models.pipeline import Pipeline
from app.models.project import Project
from app.models.user import User
from app.schemas.pipeline import PipelineCreate, PipelineResponse, PipelineUpdate

router = APIRouter()


@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    project_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Pipeline]:
    query = select(Pipeline).order_by(Pipeline.created_at.desc())
    if project_id is not None:
        query = query.where(Pipeline.project_id == project_id)
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("", response_model=PipelineResponse, status_code=status.HTTP_201_CREATED)
async def create_pipeline(
    body: PipelineCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Pipeline:
    project = await db.get(Project, body.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Project not found"
        )

    # If the pipeline is linked to a ProjectRepository, validate the link
    # belongs to the same project and inherit repo_url + branch when the
    # client didn't override them.
    project_repository_id = body.project_repository_id
    source_repo_url = body.source_repo_url
    default_branch = body.default_branch

    if project_repository_id is not None:
        from app.models.git_integration import ProjectRepository

        linked = await db.get(ProjectRepository, project_repository_id)
        if linked is None or linked.project_id != body.project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_repository_id does not belong to this project",
            )
        if not source_repo_url:
            source_repo_url = linked.repo_url
        if not default_branch or default_branch == "main":
            default_branch = linked.default_branch

    pipeline = Pipeline(
        project_id=body.project_id,
        project_repository_id=project_repository_id,
        name=body.name,
        source_repo_url=source_repo_url,
        default_branch=default_branch,
        definition_format=body.definition_format,
        yaml_content=body.yaml_content,
        created_by=current_user.id,
    )
    db.add(pipeline)
    await db.commit()
    await db.refresh(pipeline)
    return pipeline


@router.get("/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Pipeline:
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    return pipeline


@router.put("/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(
    pipeline_id: uuid.UUID,
    body: PipelineUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Pipeline:
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(pipeline, field, value)

    await db.commit()
    await db.refresh(pipeline)
    return pipeline


@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pipeline(
    pipeline_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> None:
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )
    await db.delete(pipeline)
    await db.commit()
