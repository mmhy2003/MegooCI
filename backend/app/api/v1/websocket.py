import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.config import get_settings
from app.core.security import decode_token
from app.database import async_session
from app.models.user import User
from app.models.role import UserRole
from sqlalchemy import select
from sqlalchemy.orm import selectinload

router = APIRouter()


async def _authenticate_ws(token: str | None) -> bool:
    """Validate a JWT token for WebSocket connections.

    Returns True if the token belongs to an active user with the
    ``builds.read`` permission (or admin status).
    """
    if not token:
        return False
    try:
        payload = decode_token(token)
    except ValueError:
        return False
    if payload.get("type") != "access":
        return False
    user_id_str = payload.get("sub")
    if not user_id_str:
        return False
    try:
        user_id = uuid.UUID(user_id_str)
    except ValueError:
        return False

    async with async_session() as db:
        result = await db.execute(
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.id == user_id)
        )
        user = result.scalar_one_or_none()

    if user is None or not user.is_active:
        return False
    if user.is_admin:
        return True
    for ur in user.user_roles:
        if ur.role and ur.role.permissions and "builds.read" in ur.role.permissions:
            return True
    return False


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


@router.websocket("/ws/builds/{build_id}/logs")
async def build_logs_ws(
    websocket: WebSocket,
    build_id: uuid.UUID,
    token: str | None = Query(None),
) -> None:
    if not await _authenticate_ws(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
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
    """Global build-status update stream.

    Any authenticated user can subscribe to this channel to receive
    real-time ``build_update`` events whenever a build is created,
    transitions to running, or finishes (success / failed / cancelled).
    The payload mirrors the REST BuildResponse fields needed by the
    dashboard and builds-list pages so they can patch their local state
    without a full page reload.
    """
    if not await _authenticate_ws(token):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

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
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()
