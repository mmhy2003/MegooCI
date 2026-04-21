import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_active_user
from app.database import get_db
from app.models.build import Build, Stage
from app.models.pipeline import Pipeline
from app.models.user import User
from app.schemas.build import BuildDetailResponse, BuildResponse, BuildTriggerRequest
from app.services.pipeline_compiler import parse_yaml_pipeline, compile_to_build_graph
from app.tasks.build_tasks import run_build

router = APIRouter()


@router.get("", response_model=list[BuildResponse])
async def list_builds(
    pipeline_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Build]:
    query = select(Build).order_by(Build.created_at.desc())
    if pipeline_id is not None:
        query = query.where(Build.pipeline_id == pipeline_id)
    query = query.offset(skip).limit(limit)

    result = await db.execute(query)
    return list(result.scalars().all())


@router.post("/{pipeline_id}/trigger", response_model=BuildResponse, status_code=status.HTTP_201_CREATED)
async def trigger_build(
    pipeline_id: uuid.UUID,
    body: BuildTriggerRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Build:
    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )

    if not pipeline.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Pipeline is disabled"
        )

    max_number = await db.scalar(
        select(func.coalesce(func.max(Build.number), 0)).where(
            Build.pipeline_id == pipeline_id
        )
    )
    next_number = (max_number or 0) + 1

    build = Build(
        pipeline_id=pipeline_id,
        number=next_number,
        branch=body.branch or pipeline.default_branch,
        commit_sha=body.commit_sha,
        status="pending",
        triggered_by=current_user.id,
        trigger_type="manual",
        params_json=body.params,
    )
    db.add(build)
    await db.flush()

    if pipeline.yaml_content:
        pipeline_def = parse_yaml_pipeline(pipeline.yaml_content)
        stage_defs = compile_to_build_graph(pipeline_def)

        for sort_order, stage_def in enumerate(stage_defs):
            from app.models.build import Stage, Step

            stage = Stage(
                build_id=build.id,
                name=stage_def["name"],
                status="pending",
                sort_order=sort_order,
            )
            db.add(stage)
            await db.flush()

            for step_order, step_def in enumerate(stage_def.get("steps", [])):
                step_type = step_def.get("step_type", "run")
                config = step_def.get("config", {})
                command = config.get("command") if step_type == "run" else None

                step = Step(
                    stage_id=stage.id,
                    name=step_def.get("name", f"step-{step_order}"),
                    step_type=step_type,
                    command=command,
                    config_json=config if config else None,
                    status="pending",
                    sort_order=step_order,
                )
                db.add(step)

    await db.commit()
    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    run_build.delay(str(build.id))

    return build


@router.get("/{build_id}", response_model=BuildDetailResponse)
async def get_build(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Build:
    result = await db.execute(
        select(Build)
        .where(Build.id == build_id)
        .options(selectinload(Build.stages).selectinload(Stage.steps))
    )
    build = result.scalar_one_or_none()
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )
    return build


@router.post("/{build_id}/cancel", response_model=BuildResponse)
async def cancel_build(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> Build:
    build = await db.get(Build, build_id)
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )

    if build.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel build with status '{build.status}'",
        )

    build.status = "cancelled"
    await db.commit()
    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    # If any step of this build is currently running on an agent, tell that
    # agent to stop. The local executor watches `build.status` between steps
    # and bails out on its own, but a step already in-flight on an agent
    # needs an explicit cancel frame to terminate promptly.
    await _notify_agents_of_cancel(db, build_id)

    return build


async def _notify_agents_of_cancel(
    db: AsyncSession, build_id: uuid.UUID
) -> None:
    """Publish cancel frames for every running step of `build_id` that has
    an `agent_id`. Best-effort; errors are swallowed to avoid failing the
    cancel request itself."""
    from sqlalchemy import select
    from app.models.build import Stage, Step
    from app.services.agent_dispatcher import signal_cancel_step

    result = await db.execute(
        select(Step)
        .join(Stage, Step.stage_id == Stage.id)
        .where(Stage.build_id == build_id, Step.status == "running", Step.agent_id.isnot(None))
    )
    for step in result.scalars().all():
        try:
            if step.agent_id is not None:
                await signal_cancel_step(step.agent_id, step.id)
        except Exception:
            pass


@router.post("/{build_id}/retry", response_model=BuildResponse, status_code=status.HTTP_201_CREATED)
async def retry_build(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Build:
    original = await db.execute(
        select(Build)
        .where(Build.id == build_id)
        .options(selectinload(Build.stages).selectinload(Stage.steps))
    )
    original_build = original.scalar_one_or_none()
    if original_build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )

    if original_build.status not in ("pending", "failed", "cancelled"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only retry pending, failed, or cancelled builds",
        )

    max_number = await db.scalar(
        select(func.coalesce(func.max(Build.number), 0)).where(
            Build.pipeline_id == original_build.pipeline_id
        )
    )

    from app.models.build import Step

    new_build = Build(
        pipeline_id=original_build.pipeline_id,
        number=(max_number or 0) + 1,
        branch=original_build.branch,
        commit_sha=original_build.commit_sha,
        status="pending",
        triggered_by=current_user.id,
        trigger_type="retry",
        params_json=original_build.params_json,
    )
    db.add(new_build)
    await db.flush()

    for stage in original_build.stages:
        new_stage = Stage(
            build_id=new_build.id,
            name=stage.name,
            status="pending",
            sort_order=stage.sort_order,
        )
        db.add(new_stage)
        await db.flush()

        for step in stage.steps:
            new_step = Step(
                stage_id=new_stage.id,
                name=step.name,
                step_type=step.step_type,
                command=step.command,
                config_json=step.config_json,
                status="pending",
                sort_order=step.sort_order,
            )
            db.add(new_step)

    await db.commit()
    await db.refresh(new_build)

    from app.services.search import index_build
    await index_build(new_build)

    run_build.delay(str(new_build.id))

    return new_build
