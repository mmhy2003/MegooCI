"""Agent control-plane WebSocket (PRD §6.3 / F-3.4).

An authenticated ``megooci-agent`` opens a persistent WebSocket to
``/api/v1/ws/agents/{agent_id}/connect`` and exchanges JSON frames with the
controller. Two directions:

- **Controller → Agent**: ``run_step``, ``cancel_step``, ``ping`` frames,
  sourced from the per-agent Redis list populated by
  :mod:`app.services.agent_dispatcher` and from the per-agent control
  pub/sub channel.
- **Agent → Controller**: ``hello``, ``heartbeat``, ``log``, ``step_started``,
  ``step_finished`` frames. The handler persists step state in Postgres,
  writes ``LogChunk`` rows, and republishes build logs on the existing
  ``build:{build_id}:logs`` channel so the live-build-log UI just works.

Auth is resolved from ``?token=`` (browser-friendly) or from
``X-MegooCI-Agent-Token`` / ``Authorization: Bearer`` headers (Go client).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import redis.asyncio as _aioredis_top

from app.config import get_settings
from app.core.agent_auth import (
    AgentAuthError,
    authenticate_agent_token,
    extract_ws_agent_token,
)
from app.database import async_session
from app.models.agent import Agent
from app.models.build import LogChunk, Step
from app.services.agent_dispatcher import (
    agent_control_channel,
    step_result_channel,
    tasks_list_key,
)
from app.services.in_app_notifications import get_admin_user_ids, notify_users

router = APIRouter()


# How long we wait for a new task on the agent's Redis list before looping
# back. Smaller = more CPU in idle; bigger = slower shutdown response.
_BLPOP_TIMEOUT_SECONDS = 1


@router.websocket("/ws/agents/{agent_id}/connect")
async def agent_control_ws(
    websocket: WebSocket, agent_id: uuid.UUID
) -> None:
    """Bi-directional control channel for a single connected agent."""
    token = extract_ws_agent_token(websocket)

    # Validate the agent + token before accepting the upgrade so unauthorised
    # peers get a clean close rather than an open socket. Close code 4401 is
    # our convention for auth failure; the Go client treats >=4400 as
    # terminal and exits non-zero.
    async with async_session() as db:
        try:
            agent = await authenticate_agent_token(db, agent_id, token)
        except AgentAuthError as exc:
            await websocket.close(code=4401, reason=str(exc))
            return

        agent.status = "online"
        agent.connected_at = datetime.now(timezone.utc)
        agent.last_seen_at = datetime.now(timezone.utc)
        await db.commit()
        agent_pk = agent.id

    await websocket.accept()

    # A previously-pending build that needed *this* agent's OS / labels
    # might be stuck waiting. Kick the dispatcher so it picks it up now.
    try:
        from app.services.agent_dispatcher import dispatch_pending_builds
        await dispatch_pending_builds()
    except Exception:
        pass

    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )

    # WebSocket.send_text is not safe for concurrent use. The dispatcher
    # loop and the control-pubsub loop both send, so guard with a lock.
    send_lock = asyncio.Lock()

    async def send(frame: dict) -> None:
        async with send_lock:
            await websocket.send_text(json.dumps(frame))

    # Subscribe to this agent's single control pub/sub channel for the
    # lifetime of the connection. Cancel frames are the only traffic here.
    control_pubsub = redis_client.pubsub()
    await control_pubsub.subscribe(agent_control_channel(agent_pk))

    dispatcher_task = asyncio.create_task(
        _dispatcher_loop(redis_client, agent_pk, send)
    )
    canceller_task = asyncio.create_task(_cancel_loop(control_pubsub, send))

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                continue
            await _handle_agent_frame(frame, agent_pk, redis_client)
    except WebSocketDisconnect:
        pass
    finally:
        dispatcher_task.cancel()
        canceller_task.cancel()
        for task in (dispatcher_task, canceller_task):
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        try:
            await control_pubsub.unsubscribe(agent_control_channel(agent_pk))
        except Exception:
            pass
        try:
            await control_pubsub.aclose()
        except Exception:
            pass
        try:
            await redis_client.aclose()
        except Exception:
            pass

        # Flip to offline so the dispatcher stops choosing us, and
        # notify admins that this agent disconnected. Also clear
        # current_build_id to prevent stale reservations from blocking
        # the build queue permanently.
        async with async_session() as db:
            agent = await db.get(Agent, agent_pk)
            if agent is not None:
                agent.status = "offline"
                agent.connected_at = None
                agent.current_build_id = None
                await db.commit()

                try:
                    settings = get_settings()
                    notif_redis = _aioredis_top.from_url(
                        settings.MEGOOCI_REDIS_URL, decode_responses=True
                    )
                    admin_ids = await get_admin_user_ids(db)
                    if admin_ids:
                        await notify_users(
                            db,
                            notif_redis,
                            user_ids=admin_ids,
                            type="agent_offline",
                            title=f"Agent '{agent.name}' went offline",
                            body=f"Agent '{agent.name}' disconnected and is no longer available for builds.",
                            entity_type="agent",
                            entity_id=agent.id,
                        )
                        await db.commit()
                    await notif_redis.aclose()
                except Exception:
                    pass


# ----------------------------------------------------------------------------
# Background loops
# ----------------------------------------------------------------------------
async def _dispatcher_loop(
    redis_client: aioredis.Redis,
    agent_pk: uuid.UUID,
    send: Callable[[dict], Awaitable[None]],
) -> None:
    """Continuously forward queued tasks from Redis to the connected agent."""
    key = tasks_list_key(agent_pk)
    while True:
        item = await redis_client.blpop([key], timeout=_BLPOP_TIMEOUT_SECONDS)
        if item is None:
            # Timeout — loop around so task.cancel() is responsive.
            continue
        _queue, payload = item
        try:
            frame = json.loads(payload)
        except json.JSONDecodeError:
            continue
        await send(frame)


async def _cancel_loop(
    pubsub: aioredis.client.PubSub,
    send: Callable[[dict], Awaitable[None]],
) -> None:
    """Forward cancel frames published by the API to the connected agent."""
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            frame = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        # The publisher already shapes the frame as `{"type":"cancel_step",...}`.
        await send(frame)


# ----------------------------------------------------------------------------
# Frame handlers
# ----------------------------------------------------------------------------
async def _handle_agent_frame(
    frame: dict, agent_pk: uuid.UUID, redis_client: aioredis.Redis
) -> None:
    """Process a single JSON frame received from the agent."""
    msg_type = frame.get("type")

    if msg_type == "hello":
        await _handle_hello(frame, agent_pk)
    elif msg_type == "heartbeat":
        await _handle_heartbeat(agent_pk)
    elif msg_type == "log":
        await _handle_log(frame, redis_client)
    elif msg_type == "step_started":
        await _handle_step_started(frame, agent_pk)
    elif msg_type == "step_finished":
        await _handle_step_finished(frame, redis_client)
    # Unknown frame types are silently ignored for forward-compat.


async def _handle_hello(frame: dict, agent_pk: uuid.UUID) -> None:
    version = (frame.get("version") or "")[:64] or None
    async with async_session() as db:
        agent = await db.get(Agent, agent_pk)
        if agent is None:
            return
        if version:
            agent.agent_version = version
        agent.last_seen_at = datetime.now(timezone.utc)
        await db.commit()


async def _handle_heartbeat(agent_pk: uuid.UUID) -> None:
    async with async_session() as db:
        agent = await db.get(Agent, agent_pk)
        if agent is None:
            return
        agent.last_seen_at = datetime.now(timezone.utc)
        if agent.status == "offline":
            agent.status = "online"
        await db.commit()


async def _handle_log(frame: dict, redis_client: aioredis.Redis) -> None:
    """Persist a log line and republish it on the build's live-log channel."""
    try:
        step_id = uuid.UUID(frame.get("step_id", ""))
    except (ValueError, TypeError):
        return

    build_id = frame.get("build_id")
    stream = frame.get("stream") or "stdout"
    if stream not in ("stdout", "stderr", "system"):
        stream = "stdout"
    seq = int(frame.get("seq") or 0)
    content = str(frame.get("content") or "")

    async with async_session() as db:
        chunk = LogChunk(
            step_id=step_id,
            seq=seq,
            timestamp=datetime.now(timezone.utc),
            stream=stream,
            content=content,
        )
        db.add(chunk)
        await db.commit()

    if build_id:
        await redis_client.publish(
            f"build:{build_id}:logs",
            json.dumps(
                {
                    "event": "log",
                    "step_id": str(step_id),
                    "stream": stream,
                    "seq": seq,
                    "content": content,
                }
            ),
        )


async def _handle_step_started(frame: dict, agent_pk: uuid.UUID) -> None:
    try:
        step_id = uuid.UUID(frame.get("step_id", ""))
    except (ValueError, TypeError):
        return

    async with async_session() as db:
        step = await db.get(Step, step_id)
        if step is None:
            return
        step.status = "running"
        step.started_at = datetime.now(timezone.utc)
        step.agent_id = agent_pk
        await db.commit()


async def _handle_step_finished(
    frame: dict, redis_client: aioredis.Redis
) -> None:
    try:
        step_id = uuid.UUID(frame.get("step_id", ""))
    except (ValueError, TypeError):
        return

    exit_code = frame.get("exit_code")
    exit_code_int = exit_code if isinstance(exit_code, int) else None
    status_str = frame.get("status") or (
        "success" if exit_code_int == 0 else "failed"
    )

    async with async_session() as db:
        step = await db.get(Step, step_id)
        if step is not None:
            step.status = status_str
            step.exit_code = exit_code_int
            step.finished_at = datetime.now(timezone.utc)
            await db.commit()

    # Unblock whatever dispatcher call is waiting on the result.
    await redis_client.publish(
        step_result_channel(step_id),
        json.dumps({"exit_code": exit_code_int, "status": status_str}),
    )
