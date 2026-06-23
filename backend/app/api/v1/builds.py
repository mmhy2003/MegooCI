import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import require_permission
from app.database import get_db
from app.models.build import Build, Stage
from app.models.pipeline import Pipeline
from app.models.user import User
from app.schemas.build import BuildDetailResponse, BuildResponse, BuildTriggerRequest
from app.services.build_concurrency import create_or_coalesce_build
from app.services.pipeline_compiler import (
    compile_to_build_graph,
    normalize_runs_on,
    parse_yaml_pipeline,
    validate_pipeline_definition,
)
from app.tasks.build_tasks import run_build

router = APIRouter()


@router.get("", response_model=list[BuildResponse])
async def list_builds(
    pipeline_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("builds.read")),
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
    current_user: User = Depends(require_permission("builds.manage")),
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

    # Validate the pipeline YAML before doing any work. Invalid YAML must not
    # create a build — surface the line-level errors to the caller instead.
    if pipeline.yaml_content:
        validation_errors = validate_pipeline_definition(pipeline.yaml_content)
        if validation_errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "message": "Pipeline validation failed",
                    "errors": [e.to_dict() for e in validation_errors],
                },
            )

    build, created = await create_or_coalesce_build(
        db,
        pipeline_id=pipeline_id,
        default_branch=pipeline.default_branch,
        branch=body.branch,
        commit_sha=body.commit_sha,
        params=body.params,
        triggered_by=current_user.id,
        trigger_type="manual",
    )

    if created:
        if pipeline.yaml_content:
            pipeline_def = parse_yaml_pipeline(pipeline.yaml_content)
            build.runs_on = normalize_runs_on(pipeline_def.get("runs_on"))
            stage_defs = compile_to_build_graph(pipeline_def)
            for sort_order, stage_def in enumerate(stage_defs):
                from app.models.build import Step
                stage = Stage(
                    build_id=build.id, name=stage_def["name"], status="pending",
                    sort_order=sort_order, artifact_paths=stage_def.get("artifacts"),
                )
                db.add(stage)
                await db.flush()
                for step_order, step_def in enumerate(stage_def.get("steps", [])):
                    step_type = step_def.get("step_type", "run")
                    config = step_def.get("config", {})
                    command = config.get("command") if step_type == "run" else None
                    db.add(Step(
                        stage_id=stage.id, name=step_def.get("name", f"step-{step_order}"),
                        step_type=step_type, command=command,
                        config_json=config if config else None,
                        status="pending", sort_order=step_order,
                    ))
        await db.commit()

    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    if created:
        run_build.delay(str(build.id))

    # Best-effort publish to the global builds:updates channel (unchanged).
    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.in_app_notifications import publish_build_update
    settings = get_settings()
    _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        await publish_build_update(_redis, build)
    except Exception:
        pass
    finally:
        await _redis.aclose()

    return build


@router.get("/{build_id}", response_model=BuildDetailResponse)
async def get_build(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("builds.read")),
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
    _current_user: User = Depends(require_permission("builds.manage")),
) -> Build:
    build = await db.get(Build, build_id)
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )

    if build.status not in ("pending", "queued", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel build with status '{build.status}'",
        )

    build.status = "cancelled"
    await db.commit()
    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    # Stop the running pipeline: raise the cancel flag (server-side gates poll
    # it) and push cancel frames to any agent running this build's steps. The
    # executor re-reads build.status at each step/stage boundary and bails.
    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.agent_dispatcher import signal_build_cancel
    from app.services.in_app_notifications import publish_build_update

    settings = get_settings()
    _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        await signal_build_cancel(db, build_id, _redis)
        # Best-effort: publish cancellation to the global builds:updates channel.
        try:
            await publish_build_update(_redis, build)
        except Exception:
            pass
    finally:
        await _redis.aclose()

    return build


@router.post("/{build_id}/retry", response_model=BuildResponse, status_code=status.HTTP_201_CREATED)
async def retry_build(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("builds.manage")),
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

    if original_build.status not in ("pending", "failed", "cancelled", "success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot re-run a build that is currently running",
        )

    build, created = await create_or_coalesce_build(
        db,
        pipeline_id=original_build.pipeline_id,
        default_branch=original_build.branch or "main",
        branch=original_build.branch,
        commit_sha=original_build.commit_sha,
        params=original_build.params_json,
        triggered_by=current_user.id,
        trigger_type="retry",
    )

    # Coalesced retry: absorbed into the pipeline's existing queued run (latest wins). The original build's frozen stages and runs_on are intentionally not carried over — the queued run keeps its own. (See spec: "a retry can be absorbed into the queued run".)
    if created:
        build.runs_on = original_build.runs_on
        from app.models.build import Step
        for stage in original_build.stages:
            new_stage = Stage(
                build_id=build.id, name=stage.name, status="pending",
                sort_order=stage.sort_order, artifact_paths=stage.artifact_paths,
            )
            db.add(new_stage)
            await db.flush()
            for step in stage.steps:
                db.add(Step(
                    stage_id=new_stage.id, name=step.name, step_type=step.step_type,
                    command=step.command, config_json=step.config_json,
                    status="pending", sort_order=step.sort_order,
                ))
        await db.commit()

    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    if created:
        run_build.delay(str(build.id))

    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.in_app_notifications import publish_build_update
    settings = get_settings()
    _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        await publish_build_update(_redis, build)
    except Exception:
        pass
    finally:
        await _redis.aclose()

    return build


@router.post("/{build_id}/dispatch", response_model=BuildResponse)
async def dispatch_build(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("builds.manage")),
) -> Build:
    """Manually dispatch a pending build to a free agent.

    Use this when a build is stuck in ``pending`` status and you know an
    agent is available. The endpoint pre-claims an agent and enqueues the
    build for execution.
    """
    build = await db.get(Build, build_id)
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )

    if build.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot dispatch build with status '{build.status}' (must be pending)",
        )

    # Don't dispatch while maintenance mode is active. execute_build() would
    # bounce the build straight back to pending anyway, so surface a clear
    # error here instead of reporting a misleading success.
    from app.api.v1.system import is_maintenance_mode
    if await is_maintenance_mode(db):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System is in maintenance mode. Builds cannot be dispatched "
                   "until an administrator disables maintenance mode.",
        )

    from app.services.agent_dispatcher import dispatch_single_build
    dispatched = await dispatch_single_build(build_id)

    if not dispatched:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No agent is currently available to run this build. "
                   "Ensure at least one agent is online and idle under Settings → Agents.",
        )

    await db.refresh(build)
    return build


# ------------------------------------------------------------------
# Build Logs (persisted LogChunks)
# ------------------------------------------------------------------
@router.get("/{build_id}/logs")
async def get_build_logs(
    build_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("builds.read")),
) -> list[dict]:
    """Return persisted log lines for a completed build.

    Returns a flat list of log entries across all steps, ordered by
    stage sort_order, step sort_order, then chunk seq.
    """
    from app.models.build import LogChunk, Step

    build = await db.get(Build, build_id)
    if build is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Build not found"
        )

    # Load all stages → steps → log chunks in one query.
    result = await db.execute(
        select(Stage)
        .where(Stage.build_id == build_id)
        .options(selectinload(Stage.steps))
        .order_by(Stage.sort_order)
    )
    stages = result.scalars().all()

    step_ids: list[uuid.UUID] = []
    step_meta: dict[uuid.UUID, dict] = {}
    for stage in stages:
        for step in sorted(stage.steps, key=lambda s: s.sort_order):
            step_ids.append(step.id)
            step_meta[step.id] = {
                "stage_name": stage.name,
                "step_name": step.name,
                "step_type": step.step_type,
            }

    if not step_ids:
        return []

    chunks_result = await db.execute(
        select(LogChunk)
        .where(LogChunk.step_id.in_(step_ids))
        .order_by(LogChunk.step_id, LogChunk.seq)
    )
    chunks = chunks_result.scalars().all()

    # Build output in step order (matching stage/step sort_order).
    step_order = {sid: idx for idx, sid in enumerate(step_ids)}
    sorted_chunks = sorted(
        chunks,
        key=lambda c: (step_order.get(c.step_id, 9999), c.seq),
    )

    return [
        {
            "step_id": str(c.step_id),
            "seq": c.seq,
            "timestamp": c.timestamp.isoformat() if c.timestamp else None,
            "stream": c.stream or "stdout",
            "content": c.content or "",
            **step_meta.get(c.step_id, {}),
        }
        for c in sorted_chunks
    ]

