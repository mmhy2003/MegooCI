import json
import uuid

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
    settings = get_settings()

    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    channel_name = f"build:{build_id}:logs"

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()


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
    settings = get_settings()

    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    channel_name = f"user:{user_id}:notifications"

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()


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

    await websocket.accept()
    settings = get_settings()

    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    channel_name = "builds:updates"

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue

            if is_global:
                # Admin / global permission: forward everything.
                await websocket.send_text(message["data"])
                continue

            # Project-scoped user: filter on the project_id in the payload.
            try:
                payload = json.loads(message["data"])
                raw_pid = payload.get("project_id")
            except (json.JSONDecodeError, AttributeError):
                # Malformed payload — drop.
                continue

            if raw_pid is None:
                # No project_id in payload (old caller) — drop for safety.
                continue

            try:
                event_pid = uuid.UUID(raw_pid)
            except (ValueError, AttributeError):
                continue

            if event_pid in acc:
                await websocket.send_text(message["data"])

    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()
