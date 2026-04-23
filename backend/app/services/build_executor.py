"""
Build execution service.

Runs a build by iterating through its stages and steps, dispatching each step
to the correct action handler (shell, docker, git, ssh, wait, trigger, …), streaming
output to Redis pub/sub, and persisting LogChunks.

Steps that the handler registry doesn't know about (or steps with a plain
``command`` field on agents) fall back to the legacy local shell execution
for backward compatibility.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.build import Build, LogChunk, Stage, Step
from app.services.in_app_notifications import notify_user, get_admin_user_ids
from app.services.agent_dispatcher import (
    dispatch_step_to_agent,
    pick_online_agent,
)
from app.services.step_actions import get_handler
from app.services.step_actions.base import LogLine, StepContext, StepResult
from app.services.step_actions.interpolation import (
    interpolate_value,
    load_env_vars_for_scope,
    load_secrets_for_scope,
    mask_secrets_in_log,
)


async def execute_build(
    build_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Main entry point: execute all stages/steps for a build."""
    if session_factory is None:
        from app.database import async_session

        session_factory = async_session

    settings = get_settings()
    redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    channel = f"build:{build_id}:logs"

    async with session_factory() as db:
        result = await db.execute(
            select(Build)
            .where(Build.id == build_id)
            .options(selectinload(Build.stages).selectinload(Stage.steps))
        )
        build = result.scalar_one_or_none()
        if build is None:
            return

        build.status = "running"
        build.started_at = datetime.now(timezone.utc)
        await db.commit()

        await _publish(redis_client, channel, {
            "event": "build_started",
            "build_id": str(build_id),
        })

        secrets, env_vars = await _load_scope_context(db, build)

        build_failed = False

        for stage in sorted(build.stages, key=lambda s: s.sort_order):
            if build.status == "cancelled":
                break

            stage.status = "running"
            stage.started_at = datetime.now(timezone.utc)
            await db.commit()

            await _publish(redis_client, channel, {
                "event": "stage_started",
                "stage_id": str(stage.id),
                "stage_name": stage.name,
            })

            stage_failed = False

            for step in sorted(stage.steps, key=lambda s: s.sort_order):
                if build.status == "cancelled":
                    break

                step.status = "running"
                step.started_at = datetime.now(timezone.utc)
                await db.commit()

                await _publish(redis_client, channel, {
                    "event": "step_started",
                    "step_id": str(step.id),
                    "step_name": step.name,
                    "step_type": step.step_type,
                })

                step_result = await _execute_step(
                    step=step,
                    stage=stage,
                    build=build,
                    secrets=secrets,
                    env_vars=env_vars,
                    db=db,
                    redis_client=redis_client,
                    channel=channel,
                )

                step.exit_code = step_result.exit_code
                step.status = step_result.status
                step.finished_at = datetime.now(timezone.utc)
                await db.commit()

                await _publish(redis_client, channel, {
                    "event": "step_finished",
                    "step_id": str(step.id),
                    "status": step.status,
                    "exit_code": step.exit_code,
                })

                if step.status == "failed":
                    stage_failed = True
                    break

            stage.status = "failed" if stage_failed else "success"
            stage.finished_at = datetime.now(timezone.utc)
            await db.commit()

            await _publish(redis_client, channel, {
                "event": "stage_finished",
                "stage_id": str(stage.id),
                "status": stage.status,
            })

            if stage_failed:
                build_failed = True
                break

        await db.refresh(build)
        if build.status == "cancelled":
            final_status = "cancelled"
        elif build_failed:
            final_status = "failed"
        else:
            final_status = "success"

        build.status = final_status
        build.finished_at = datetime.now(timezone.utc)
        await db.commit()

        await _publish(redis_client, channel, {
            "event": "build_finished",
            "build_id": str(build_id),
            "status": final_status,
        })

        await _send_build_finished_notification(
            db, redis_client, build, final_status
        )

    await redis_client.aclose()


async def _execute_step(
    step: Step,
    stage: Stage,
    build: Build,
    secrets: dict[str, str],
    env_vars: dict[str, str],
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
) -> StepResult:
    """Dispatch a step to the correct handler.

    Priority:
    1. Look up a registered handler for ``step.step_type``.
    2. If ``step.step_type == "run"`` and an agent is online, dispatch to agent.
    3. Fall back to legacy local shell execution for ``run`` steps.
    """
    from app.models.pipeline import Pipeline

    handler = get_handler(step.step_type)

    step_config = dict(step.config_json or {})
    if step.step_type == "run" and not step_config.get("command") and step.command:
        step_config["command"] = step.command

    step_config = interpolate_value(step_config, secrets, env_vars)
    merged_env = {**env_vars}
    merged_env = interpolate_value(merged_env, secrets, env_vars)

    pipeline = await db.get(Pipeline, build.pipeline_id)
    project_id = pipeline.project_id if pipeline else build.pipeline_id

    # Notify and trigger_pipeline steps need DB access — always run server-side.
    if step.step_type in ("notify", "trigger_pipeline") and handler is not None:
        ctx = StepContext(
            build_id=build.id,
            step_id=step.id,
            step_name=step.name,
            stage_name=stage.name,
            pipeline_id=build.pipeline_id,
            project_id=project_id,
            branch=build.branch,
            commit_sha=build.commit_sha,
            env=merged_env,
            secrets=secrets,
        )
        return await _run_handler(handler, step_config, ctx, step, db, redis_client, channel, secrets)

    if step.step_type == "run":
        agent_result = await _try_dispatch_to_agent(
            step, stage.name, build.id, db
        )
        if agent_result is not None:
            await db.refresh(step)
            return StepResult(
                exit_code=step.exit_code or 0,
                status=step.status,
            )

    if handler is None:
        if step.command:
            exit_code = await _run_command_legacy(step, db, redis_client, channel, secrets)
            return StepResult(
                exit_code=exit_code,
                status="success" if exit_code == 0 else "failed",
            )
        return StepResult(
            exit_code=1,
            status="failed",
            error=f"No handler for step type '{step.step_type}'",
        )

    ctx = StepContext(
        build_id=build.id,
        step_id=step.id,
        step_name=step.name,
        stage_name=stage.name,
        pipeline_id=build.pipeline_id,
        project_id=project_id,
        branch=build.branch,
        commit_sha=build.commit_sha,
        env=merged_env,
        secrets=secrets,
    )

    return await _run_handler(handler, step_config, ctx, step, db, redis_client, channel, secrets)


async def _run_handler(
    handler,
    config: dict[str, Any],
    ctx: StepContext,
    step: Step,
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
    secrets: dict[str, str],
) -> StepResult:
    """Execute a handler's async generator and persist log lines."""
    seq = 0
    final_result = StepResult(exit_code=1, status="failed", error="Handler produced no result")

    try:
        async for item in handler.execute(config, ctx, db):
            if isinstance(item, StepResult):
                final_result = item
            elif isinstance(item, LogLine):
                seq += 1
                now = datetime.now(timezone.utc)
                content = mask_secrets_in_log(item.content, secrets)

                log_chunk = LogChunk(
                    step_id=step.id,
                    seq=seq,
                    timestamp=now,
                    stream=item.stream,
                    content=content,
                )
                db.add(log_chunk)

                await _publish(redis_client, channel, {
                    "event": "log",
                    "step_id": str(step.id),
                    "stream": item.stream,
                    "seq": seq,
                    "content": content,
                })

        await db.commit()
    except Exception as exc:
        seq += 1
        log_chunk = LogChunk(
            step_id=step.id,
            seq=seq,
            timestamp=datetime.now(timezone.utc),
            stream="stderr",
            content=f"Handler error: {exc}\n",
        )
        db.add(log_chunk)
        await db.commit()

        await _publish(redis_client, channel, {
            "event": "log",
            "step_id": str(step.id),
            "stream": "stderr",
            "seq": seq,
            "content": f"Handler error: {exc}\n",
        })
        final_result = StepResult(exit_code=1, status="failed", error=str(exc))

    return final_result


async def _load_scope_context(
    db: AsyncSession, build: Build
) -> tuple[dict[str, str], dict[str, str]]:
    """Load secrets and env vars scoped to this build's pipeline/project."""
    from app.models.pipeline import Pipeline

    pipeline = await db.get(Pipeline, build.pipeline_id)
    project_id = pipeline.project_id if pipeline else build.pipeline_id

    secrets = await load_secrets_for_scope(db, project_id, build.pipeline_id)
    env_vars = await load_env_vars_for_scope(db, project_id, build.pipeline_id)
    return secrets, env_vars


async def _run_command_legacy(
    step: Step,
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
    secrets: dict[str, str],
) -> int:
    """Legacy local shell execution (backward compatibility fallback)."""
    seq = 0

    try:
        process = await asyncio.create_subprocess_shell(
            step.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def _read_stream(
            stream: asyncio.StreamReader, stream_name: str
        ) -> None:
            nonlocal seq
            async for line_bytes in stream:
                line = line_bytes.decode(errors="replace")
                line = mask_secrets_in_log(line, secrets)
                seq += 1
                now = datetime.now(timezone.utc)

                log_chunk = LogChunk(
                    step_id=step.id,
                    seq=seq,
                    timestamp=now,
                    stream=stream_name,
                    content=line,
                )
                db.add(log_chunk)

                await _publish(redis_client, channel, {
                    "event": "log",
                    "step_id": str(step.id),
                    "stream": stream_name,
                    "seq": seq,
                    "content": line,
                })

        await asyncio.gather(
            _read_stream(process.stdout, "stdout"),
            _read_stream(process.stderr, "stderr"),
        )
        await process.wait()
        await db.commit()

        return process.returncode or 0

    except Exception as exc:
        seq += 1
        log_chunk = LogChunk(
            step_id=step.id,
            seq=seq,
            timestamp=datetime.now(timezone.utc),
            stream="stderr",
            content=f"Execution error: {exc}\n",
        )
        db.add(log_chunk)
        await db.commit()

        await _publish(redis_client, channel, {
            "event": "log",
            "step_id": str(step.id),
            "stream": "stderr",
            "seq": seq,
            "content": f"Execution error: {exc}\n",
        })
        return 1


async def _publish(
    redis_client: aioredis.Redis, channel: str, data: dict
) -> None:
    """Publish a JSON message to a Redis pub/sub channel."""
    await redis_client.publish(channel, json.dumps(data))


async def _try_dispatch_to_agent(
    step: Step,
    stage_name: str,
    build_id: uuid.UUID,
    db: AsyncSession,
) -> dict | None:
    """If a healthy agent is online, run this step there and return its
    result dict. Returns None if no agent picks it up within the timeout,
    so the caller can transparently fall back to local execution.
    """
    agent = await pick_online_agent(db)
    if agent is None:
        return None
    return await dispatch_step_to_agent(
        step=step,
        stage_name=stage_name,
        build_id=build_id,
        agent_id=agent.id,
    )


async def _send_build_finished_notification(
    db: AsyncSession,
    redis_client: aioredis.Redis,
    build: Build,
    final_status: str,
) -> None:
    """Send an in-app notification to the user who triggered the build."""
    if build.triggered_by is None:
        return

    status_label = {"success": "succeeded", "failed": "failed", "cancelled": "was cancelled"}
    verb = status_label.get(final_status, final_status)

    try:
        await notify_user(
            db,
            redis_client,
            user_id=build.triggered_by,
            type=f"build_{final_status}",
            title=f"Build #{build.number} {verb}",
            body=f"Build #{build.number} on branch {build.branch or 'default'} {verb}.",
            entity_type="build",
            entity_id=build.id,
        )
        await db.commit()
    except Exception:
        await db.rollback()
