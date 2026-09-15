import asyncio
import json
import logging
import uuid
from collections.abc import Callable

import redis.asyncio as aioredis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.config import get_settings
from app.core.access import ALL_PROJECTS, accessible_project_ids, project_id_for_build
from app.core.security import decode_token
from app.database import async_session
from app.models.user import User
from app.models.role import UserRole
from sqlalchemy import select
from sqlalchemy.orm import selectinload

router = APIRouter()
logger = logging.getLogger(__name__)

# WebSocket close codes (outside the standard 1xxx range so the browser can
# distinguish auth failures from normal closes).
_WS_UNAUTHORIZED = 4401
_WS_FORBIDDEN = 4403
_WS_NOT_FOUND = 4404


async def _load_ws_user(token: str | None) -> User | None:
    """Validate a JWT and return the fully-loaded User (with user_roles), or None."""
    if not token:
        return None
    try:
        payload = decode_token(token)
    except ValueError:
        return None
    if payload.get("type") != "access":
        return None
    user_id_str = payload.get("sub")
    if not user_id_str:
        return None
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return None

    async with async_session() as db:
        result = await db.execute(
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        return None
    return user


async def _authenticate_ws_user(token: str | None) -> uuid.UUID | None:
    """Validate a JWT and return the user_id if active, else None."""
    if not token:
        return None
    try:
        payload = decode_token(token)
    except ValueError:
        return None
    if payload.get("type") != "access":
        return None
    user_id_str = payload.get("sub")
    if not user_id_str:
        return None
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return None

    async with async_session() as db:
        result = await db.execute(
            select(User.id, User.is_active).where(User.id == user_id)
        )
        row = result.one_or_none()

    if row is None or not row.is_active:
        return None
    return row.id


def _can_read_builds_globally(user: User) -> bool:
    """True if the user has builds.read globally (admin or global role)."""
    if user.is_admin:
        return True
    for ur in user.user_roles:
        if ur.scope_type == "global" and ur.role and ur.role.permissions:
            if "builds.read" in ur.role.permissions:
                return True
    return False


async def _close_quietly(websocket: WebSocket, code: int) -> None:
    try:
        await websocket.close(code=code)
    except Exception:
        # Already closed by the client or the server.
        pass


async def _relay_pubsub(
    websocket: WebSocket,
    channel_name: str,
    transform: Callable[[str], str | None] = lambda data: data,
) -> None:
    """Forward Redis pub/sub messages on *channel_name* to an accepted WebSocket.

    ``transform`` maps each payload to the text to send, or ``None`` to drop it.

    Returns as soon as either side goes away, always releasing the Redis
    connection. A handler that only awaits ``pubsub.listen()`` notices a
    browser disconnect on its next send; on a quiet channel (a finished build's
    logs, a user with no notifications, a scoped user whose events are all
    filtered) that never comes, and the Redis socket leaks until the process
    runs out of file descriptors.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    pubsub = redis_client.pubsub()
    try:
        try:
            await pubsub.subscribe(channel_name)
        except Exception:
            logger.warning(
                "WebSocket relay could not subscribe to %s", channel_name, exc_info=True
            )
            await _close_quietly(websocket, status.WS_1011_INTERNAL_ERROR)
            return

        async def forward() -> None:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                text = transform(message["data"])
                if text is not None:
                    await websocket.send_text(text)

        async def wait_for_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        forwarder = asyncio.create_task(forward())
        watcher = asyncio.create_task(wait_for_disconnect())
        try:
            await asyncio.wait(
                {forwarder, watcher}, return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            forwarder.cancel()
            watcher.cancel()
            results = await asyncio.gather(forwarder, watcher, return_exceptions=True)

        if watcher.cancelled():
            # The Redis side ended first (e.g. Redis restarted). Close so the
            # browser's reconnect logic kicks in instead of a silent dead socket.
            err = results[0]
            if isinstance(err, Exception) and not isinstance(err, WebSocketDisconnect):
                logger.warning(
                    "WebSocket relay for %s stopped", channel_name, exc_info=err
                )
            await _close_quietly(websocket, status.WS_1011_INTERNAL_ERROR)
    finally:
        try:
            await pubsub.aclose()
        except Exception:
            pass
        try:
            await redis_client.aclose()
        except Exception:
            pass


@router.websocket("/ws/builds/{build_id}/logs")
async def build_logs_ws(
    websocket: WebSocket,
    build_id: uuid.UUID,
    token: str | None = Query(None),
) -> None:
    user = await _load_ws_user(token)
    if user is None:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    # Resolve the build's project and check scoped access.
    async with async_session() as db:
        pid = await project_id_for_build(db, build_id)

    if pid is None:
        await websocket.close(code=_WS_NOT_FOUND)
        return

    acc = accessible_project_ids(user, "builds.read")
    if acc is not ALL_PROJECTS and pid not in acc:
        await websocket.close(code=_WS_FORBIDDEN)
        return

    await websocket.accept()
    await _relay_pubsub(websocket, f"build:{build_id}:logs")


@router.websocket("/ws/notifications")
async def user_notifications_ws(
    websocket: WebSocket,
    token: str | None = Query(None),
) -> None:
    """Per-user notification stream.

    Subscribes to ``user:{user_id}:notifications`` on Redis pub/sub and
    forwards every published JSON payload to the connected browser client.
    """
    user_id = await _authenticate_ws_user(token)
    if user_id is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await _relay_pubsub(websocket, f"user:{user_id}:notifications")


@router.websocket("/ws/builds/updates")
async def build_updates_ws(
    websocket: WebSocket,
    token: str | None = Query(None),
) -> None:
    """Global build-status update stream, filtered to projects the user can see.

    Subscribes to the ``builds:updates`` Redis channel. Before forwarding each
    event the handler checks whether the user has ``builds.read`` access to the
    event's project (carried in the payload as ``project_id``).

    - Admins and users with a global ``builds.read`` grant receive all events.
    - Project-scoped users only receive events for their accessible projects.
    - Events whose payload lacks a ``project_id`` (older callers not yet
      updated) are dropped for scoped users to avoid inadvertent leaks.
    """
    user = await _load_ws_user(token)
    if user is None:
        await websocket.close(code=_WS_UNAUTHORIZED)
        return

    # Snapshot access set ONCE at connect time — avoids per-event DB hits.
    acc = accessible_project_ids(user, "builds.read")
    is_global = acc is ALL_PROJECTS

    def visible_to_user(data: str) -> str | None:
        if is_global:
            # Admin / global permission: forward everything.
            return data

        # Project-scoped user: filter on the project_id in the payload.
        try:
            payload = json.loads(data)
            raw_pid = payload.get("project_id")
        except (json.JSONDecodeError, AttributeError):
            # Malformed payload — drop.
            return None

        if raw_pid is None:
            # No project_id in payload (old caller) — drop for safety.
            return None

        try:
            event_pid = uuid.UUID(raw_pid)
        except (ValueError, AttributeError, TypeError):
            return None

        return data if event_pid in acc else None

    await websocket.accept()
    await _relay_pubsub(websocket, "builds:updates", visible_to_user)
