"""Controller-side dispatcher that hands build steps to remote agents.

Design (PRD §6.3 / F-3.4 / F-3.6):

- Each registered agent owns a Redis **list** at ``agent:{id}:tasks``. The
  WebSocket handler for that agent BLPOPs from it and forwards messages
  over the socket.
- Step completion is signalled back to the dispatcher through a Redis
  **pub/sub** channel at ``step:{step_id}:result``. The payload is a small
  JSON document: ``{"exit_code": int, "status": "success"|"failed"}``.
- Log lines from the agent are ingested by the WebSocket handler directly
  (see ``api/v1/agents_ws.py``) — they go into the same
  ``build:{build_id}:logs`` pub/sub channel the local executor already uses
  and are persisted as ``LogChunk`` rows, so the existing UI works
  unchanged.

The dispatcher is intentionally simple in Phase 1:

- **Agent selection**: any agent whose ``status == 'online'`` and whose
  ``connected_at is not None`` is a candidate. If multiple match, we pick
  the one with the oldest ``last_seen_at`` (a cheap round-robin proxy).
  Label-based selection is deferred to a follow-up.
- **No reservation protocol**: we optimistically dispatch to the chosen
  agent and trust its local semaphore to respect ``capacity``. If the
  agent disconnects mid-step, the controller times out waiting on the
  completion channel and the build executor falls back to local.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.agent import Agent
from app.models.build import Step


# How long we wait for a dispatch to produce a result before giving up. This
# bound must be high enough for real build steps (think `docker build`,
# `npm install`) but low enough that a half-dead agent doesn't hold a build
# forever. The caller (build executor) is expected to fall back to local
# execution on timeout.
_DEFAULT_STEP_TIMEOUT_SECONDS = 60 * 60  # 1 hour


def tasks_list_key(agent_id: uuid.UUID | str) -> str:
    """Redis LIST that an agent's WS handler BLPOPs task frames from."""
    return f"agent:{agent_id}:tasks"


def agent_control_channel(agent_id: uuid.UUID | str) -> str:
    """Per-agent Redis pub/sub channel for out-of-band control messages
    (today: cancel). Subscribed once for the lifetime of the WS session."""
    return f"agent:{agent_id}:control"


def step_result_channel(step_id: uuid.UUID | str) -> str:
    """Per-step pub/sub channel the agent writes to on completion."""
    return f"step:{step_id}:result"


async def pick_online_agent(db: AsyncSession) -> Agent | None:
    """Return a currently-online agent or None.

    An agent is considered "currently online" if it has an active WS session
    (``connected_at is not None``) and its status is ``online``. Picks the
    least-recently-used online agent as a poor-man's load balancer.
    """
    result = await db.execute(
        select(Agent)
        .where(Agent.status == "online", Agent.connected_at.isnot(None))
        .order_by(Agent.last_seen_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def dispatch_step_to_agent(
    step: Step,
    stage_name: str,
    build_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    artifact_paths: list[str] | None = None,
    timeout_seconds: int = _DEFAULT_STEP_TIMEOUT_SECONDS,
    config_override: dict[str, Any] | None = None,
    command_override: str | None = None,
) -> dict[str, Any] | None:
    """Send a run_step message to the given agent and wait for its result.

    Returns the result dict (``{"exit_code": int, "status": "success"|"failed"}``)
    or ``None`` on timeout / infrastructure failure. Logs streamed back from
    the agent are handled separately by the WebSocket ingestion handler.

    When *config_override* or *command_override* are provided they replace
    the raw DB values so that server-side interpolation (secrets, env-vars)
    and enrichment (token resolution) are reflected in what the agent sees.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )

    payload: dict[str, Any] = {
        "type": "run_step",
        "assignment_id": str(uuid.uuid4()),
        "build_id": str(build_id),
        "stage_name": stage_name,
        "step_id": str(step.id),
        "step_name": step.name,
        "step_type": step.step_type or "run",
        "command": command_override if command_override is not None else (step.command or ""),
        "config": config_override if config_override is not None else (step.config_json or {}),
    }
    if artifact_paths:
        payload["artifact_paths"] = artifact_paths

    result_channel = step_result_channel(step.id)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(result_channel)

    try:
        # Enqueue the task for the agent's WS handler to pick up.
        await redis_client.rpush(tasks_list_key(agent_id), json.dumps(payload))

        # Wait for the agent to report completion.
        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        return json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
        except asyncio.TimeoutError:
            return None
        return None
    finally:
        await pubsub.unsubscribe(result_channel)
        await pubsub.aclose()
        await redis_client.aclose()


async def signal_cancel_step(agent_id: uuid.UUID, step_id: uuid.UUID) -> None:
    """Tell a specific agent to stop running the given step.

    Publishes on the agent's control channel; the agent's WS handler forwards
    the cancel frame over the socket.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    try:
        await redis_client.publish(
            agent_control_channel(agent_id),
            json.dumps({"type": "cancel_step", "step_id": str(step_id)}),
        )
    finally:
        await redis_client.aclose()


async def send_build_finished(agent_id: uuid.UUID, build_id: uuid.UUID) -> None:
    """Tell the agent that a build is fully complete so it can release the
    shared workspace directory.

    Pushes a ``build_finished`` frame onto the agent's task queue — the same
    queue the dispatcher loop reads from — so it is delivered over the WS
    connection in order after any pending step assignments.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    try:
        await redis_client.rpush(
            tasks_list_key(agent_id),
            json.dumps({
                "type": "build_finished",
                "build_id": str(build_id),
            }),
        )
    finally:
        await redis_client.aclose()
