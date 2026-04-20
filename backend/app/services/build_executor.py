"""
Build execution service.

Runs a build by iterating through its stages and steps, executing shell commands
via subprocess, streaming output to Redis pub/sub, and persisting LogChunks.
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.build import Build, LogChunk, Stage, Step
from app.services.agent_dispatcher import (
    dispatch_step_to_agent,
    pick_online_agent,
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
                })

                if step.command:
                    # Try to dispatch to a connected agent first. If no agent
                    # is online, or if the agent times out / disconnects, fall
                    # back to running the command locally in the Celery worker
                    # (the pre-agent behaviour).
                    agent_result = await _try_dispatch_to_agent(
                        step, stage.name, build.id, db
                    )
                    if agent_result is not None:
                        # The agent's WS handler has already persisted
                        # step.status, step.started_at, step.finished_at, and
                        # exit_code, and has streamed log lines into the same
                        # build pubsub channel. Refresh the ORM instance so
                        # the downstream status comparisons are accurate.
                        await db.refresh(step)
                    else:
                        exit_code = await _run_command(
                            step, db, redis_client, channel
                        )
                        step.exit_code = exit_code
                        step.status = "success" if exit_code == 0 else "failed"
                        step.finished_at = datetime.now(timezone.utc)
                        await db.commit()
                else:
                    step.status = "success"
                    step.exit_code = 0
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

    await redis_client.aclose()


async def _run_command(
    step: Step,
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
) -> int:
    """Execute a shell command, stream output, and persist log chunks."""
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
