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
from app.services.in_app_notifications import notify_user, get_admin_user_ids, publish_build_update
from app.services.agent_dispatcher import (
    claim_agent,
    dispatch_pending_builds,
    dispatch_step_to_agent,
    pick_online_agent,
    release_agent,
    send_build_finished,
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
    """Main entry point: execute all stages/steps for a build.

    Before executing, the function attempts to claim an idle agent. If no
    agent is available the build stays in ``pending`` and will be picked up
    later by ``dispatch_pending_builds()`` (called whenever another build
    finishes and frees an agent).
    """
    if session_factory is None:
        from app.database import async_session

        session_factory = async_session

    settings = get_settings()
    redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    channel = f"build:{build_id}:logs"

    # ── Agent reservation ────────────────────────────────────────────────
    # We must claim an idle agent *before* marking the build as running.
    # If no agent is available, the build stays pending and will be
    # dequeued automatically when an agent finishes its current work.
    claimed_agent_id: uuid.UUID | None = None

    async with session_factory() as db:
        result = await db.execute(
            select(Build).where(Build.id == build_id)
        )
        build = result.scalar_one_or_none()
        if build is None:
            await redis_client.aclose()
            return

        # If the build was already cancelled or is no longer pending, bail.
        if build.status != "pending":
            await redis_client.aclose()
            return

        agent = await pick_online_agent(db)
        if agent is not None:
            claimed = await claim_agent(db, agent.id, build_id)
            if claimed:
                claimed_agent_id = agent.id

        if claimed_agent_id is None:
            # No idle agent — leave the build as pending. It will be
            # dispatched by dispatch_pending_builds() when an agent
            # finishes its current build.
            await redis_client.aclose()
            return

    # ── Execute ──────────────────────────────────────────────────────────
    try:
        await _run_build_stages(
            build_id=build_id,
            session_factory=session_factory,
            redis_client=redis_client,
            channel=channel,
        )
    finally:
        # Always release the agent and try to dequeue a pending build,
        # regardless of whether the build succeeded, failed, or crashed.
        if claimed_agent_id is not None:
            try:
                async with session_factory() as db:
                    await release_agent(db, claimed_agent_id, build_id)
            except Exception:
                pass  # non-fatal; the agent will be released on disconnect

            # Tell the agent to clean up its workspace.
            try:
                await send_build_finished(claimed_agent_id, build_id)
            except Exception:
                pass

        await redis_client.aclose()

        # Kick the next pending build now that an agent is free.
        try:
            await dispatch_pending_builds()
        except Exception:
            pass


async def _run_build_stages(
    build_id: uuid.UUID,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: aioredis.Redis,
    channel: str,
) -> None:
    """Inner routine that actually executes all stages/steps for a build."""

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
        # Best-effort: publish to the global builds:updates fan-out channel.
        # Wrapped in try/except so a Redis hiccup can never crash the executor.
        try:
            await publish_build_update(redis_client, build)
        except Exception:
            pass

        secrets, env_vars, builtins = await _load_scope_context(db, build)

        build_failed = False
        build_agent_ids: set[uuid.UUID] = set()

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
                    builtins=builtins,
                    db=db,
                    redis_client=redis_client,
                    channel=channel,
                    build_agent_ids=build_agent_ids,
                )

                # Persist error messages as visible log lines so users
                # see exactly why a step failed in the build UI.
                if step_result.error:
                    await _emit_system_log(
                        step, db, redis_client, channel,
                        f"\u274c {step_result.error}",
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
        # Best-effort: publish to the global builds:updates fan-out channel.
        try:
            await publish_build_update(redis_client, build)
        except Exception:
            pass

        # Tell every agent that participated in this build to release its
        # shared workspace directory.
        for agent_id in build_agent_ids:
            try:
                await send_build_finished(agent_id, build_id)
            except Exception:
                pass  # best-effort; the agent has a safety-net timer

        await _send_build_finished_notification(
            db, redis_client, build, final_status
        )


async def _execute_step(
    step: Step,
    stage: Stage,
    build: Build,
    secrets: dict[str, str],
    env_vars: dict[str, str],
    builtins: dict[str, dict[str, str]],
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
    build_agent_ids: set[uuid.UUID] | None = None,
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

    step_config = interpolate_value(step_config, secrets, env_vars, builtins)
    merged_env = {**env_vars}
    merged_env = interpolate_value(merged_env, secrets, env_vars, builtins)

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

    # ── Agent dispatch ───────────────────────────────────────────────────
    # All step types except notify/trigger_pipeline can be executed on a
    # remote agent.  Infrastructure steps (git_clone, docker_build,
    # ssh_exec, etc.) MUST run on the agent because the server container
    # typically lacks the required tools (git, docker, ssh).
    # Only send artifact_paths on the last step so files are collected
    # after all steps have run.
    _SERVER_ONLY_TYPES = {"notify", "trigger_pipeline", "wait_webhook", "wait_input"}

    if step.step_type not in _SERVER_ONLY_TYPES:
        sorted_steps = sorted(stage.steps, key=lambda s: s.sort_order)
        is_last_step = step.id == sorted_steps[-1].id if sorted_steps else False
        art_paths = stage.artifact_paths if is_last_step else None

        # Pre-dispatch config enrichment: resolve server-side secrets
        # that the agent cannot look up on its own.
        dispatch_config = dict(step_config)  # already interpolated
        await _enrich_config_for_agent(
            step.step_type, dispatch_config, secrets, project_id, db
        )

        agent_result, agent_id = await _try_dispatch_to_agent(
            step, stage.name, build.id, db,
            artifact_paths=art_paths,
            redis_client=redis_client,
            channel=channel,
            config_override=dispatch_config,
            command_override=step_config.get("command") or step.command or "",
        )
        if agent_result is not None:
            if agent_id is not None and build_agent_ids is not None:
                build_agent_ids.add(agent_id)
            await db.refresh(step)
            return StepResult(
                exit_code=step.exit_code or 0,
                status=step.status,
            )

        # Agent dispatch returned None — no agent online or timeout.
        # Fall through to server-side handler if one exists.

    if handler is None:
        if step.command:
            await _emit_system_log(
                step, db, redis_client, channel,
                "No build agent is online. Falling back to server-side execution.\n"
                "To run steps on a dedicated agent, register one under Settings \u2192 Agents.",
            )
            exit_code = await _run_command_legacy(step, db, redis_client, channel, secrets)
            return StepResult(
                exit_code=exit_code,
                status="success" if exit_code == 0 else "failed",
            )
        return StepResult(
            exit_code=1,
            status="failed",
            error=f"No handler registered for step type '{step.step_type}' and no build agent is online.\n"
                  f"Check your pipeline YAML or register an agent under Settings \u2192 Agents.",
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
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, str]]]:
    """Load secrets, env vars, and built-in variables for a build.

    Returns (secrets, env_vars, builtins) where *builtins* has the structure::

        {
            "build":    {"branch": ..., "number": ..., ...},
            "pipeline": {"name": ..., "id": ...},
            "project":  {"name": ..., "slug": ..., "id": ...},
        }
    """
    from app.models.pipeline import Pipeline
    from app.models.project import Project

    pipeline = await db.get(Pipeline, build.pipeline_id)
    project_id = pipeline.project_id if pipeline else build.pipeline_id
    project = await db.get(Project, project_id) if project_id else None

    secrets = await load_secrets_for_scope(db, project_id, build.pipeline_id)
    env_vars = await load_env_vars_for_scope(db, project_id, build.pipeline_id)

    settings = get_settings()

    # ── Built-in variables ───────────────────────────────────────────────
    builtins: dict[str, dict[str, str]] = {
        "build": {
            "id": str(build.id),
            "number": str(build.number),
            "branch": build.branch or "",
            "commit": build.commit_sha or "",
            "status": build.status or "",
            "trigger": build.trigger_type or "manual",
            "created_at": build.created_at.isoformat() if build.created_at else "",
            "started_at": build.started_at.isoformat() if build.started_at else "",
        },
        "pipeline": {
            "id": str(build.pipeline_id),
            "name": pipeline.name if pipeline else "",
            "repo_url": pipeline.source_repo_url or "" if pipeline else "",
            "default_branch": pipeline.default_branch if pipeline else "",
        },
        "project": {
            "id": str(project_id),
            "name": project.name if project else "",
            "slug": project.slug if project else "",
        },
        "megooci": {
            "url": settings.MEGOOCI_PUBLIC_URL or "",
        },
    }

    return secrets, env_vars, builtins


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


async def _emit_system_log(
    step: Step,
    db: AsyncSession,
    redis_client: aioredis.Redis,
    channel: str,
    message: str,
) -> None:
    """Write a system-level log line visible in the build UI.

    Persists a LogChunk row and publishes to Redis so the real-time feed
    picks it up immediately.
    """
    from sqlalchemy import func as sa_func

    # Determine the next seq value for this step.
    max_seq = await db.scalar(
        select(sa_func.coalesce(sa_func.max(LogChunk.seq), 0))
        .where(LogChunk.step_id == step.id)
    )
    seq = (max_seq or 0) + 1
    now = datetime.now(timezone.utc)

    content = message if message.endswith("\n") else f"{message}\n"

    log_chunk = LogChunk(
        step_id=step.id,
        seq=seq,
        timestamp=now,
        stream="system",
        content=content,
    )
    db.add(log_chunk)
    await db.commit()

    await _publish(redis_client, channel, {
        "event": "log",
        "step_id": str(step.id),
        "stream": "system",
        "seq": seq,
        "content": content,
    })


async def _try_dispatch_to_agent(
    step: Step,
    stage_name: str,
    build_id: uuid.UUID,
    db: AsyncSession,
    *,
    artifact_paths: list[str] | None = None,
    redis_client: aioredis.Redis | None = None,
    channel: str | None = None,
    config_override: dict | None = None,
    command_override: str | None = None,
) -> tuple[dict | None, uuid.UUID | None]:
    """If a healthy agent is online, run this step there and return
    ``(result_dict, agent_id)``. Returns ``(None, None)`` if no agent picks
    it up within the timeout, so the caller can transparently fall back to
    local execution.

    Emits descriptive system log lines visible in the build UI when the
    agent lookup or dispatch fails.
    """
    agent = await pick_online_agent(db)
    if agent is None:
        if redis_client and channel:
            await _emit_system_log(
                step, db, redis_client, channel,
                "\u26a0\ufe0f No build agent is currently online. "
                "Register and start an agent under Settings \u2192 Agents, "
                "or the step will run on the server (if supported).",
            )
        return None, None

    if redis_client and channel:
        await _emit_system_log(
            step, db, redis_client, channel,
            f"\u2192 Dispatching to agent \u2018{agent.name}\u2019 ({str(agent.id)[:8]}\u2026)",
        )

    result = await dispatch_step_to_agent(
        step=step,
        stage_name=stage_name,
        build_id=build_id,
        agent_id=agent.id,
        artifact_paths=artifact_paths,
        config_override=config_override,
        command_override=command_override,
    )

    if result is None and redis_client and channel:
        await _emit_system_log(
            step, db, redis_client, channel,
            f"\u26a0\ufe0f Agent \u2018{agent.name}\u2019 did not respond within the timeout. "
            "The agent may have disconnected or the step took too long. "
            "Falling back to server-side execution.",
        )

    return result, agent.id if result is not None else None


async def _enrich_config_for_agent(
    step_type: str,
    config: dict,
    secrets: dict[str, str],
    project_id: Any,
    db: AsyncSession,
) -> None:
    """Mutate *config* in-place to resolve server-side secrets that the
    agent cannot look up on its own.

    Currently handles:
    - ``git_clone``: resolves the authentication token via the three-tier
      strategy (explicit → GIT_TOKEN secret → auto-inject from provider).
    - ``docker_login`` / ``docker_push``: when the target registry matches
      the built-in MegooCI registry host, injects ``_internal_registry``
      so the agent can push via the internal Docker network and bypass
      external reverse proxies (Cloudflare, Nginx) that impose upload
      size limits.
    """
    settings = get_settings()

    if step_type == "git_clone":
        from app.services.step_actions.git import _resolve_git_token
        from app.services.step_actions.base import StepContext

        # Build a lightweight context for token resolution.
        mini_ctx = StepContext(
            build_id=uuid.UUID(int=0),
            step_id=uuid.UUID(int=0),
            step_name="",
            stage_name="",
            pipeline_id=uuid.UUID(int=0),
            project_id=project_id,
            branch=config.get("branch", "main"),
            commit_sha=None,
            env={},
            secrets=secrets,
        )
        repo = config.get("repo", "")
        token = await _resolve_git_token(repo, config, mini_ctx, db)
        if token:
            config["token"] = token


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
