"""In-app notification dispatcher.

Provides helpers to create `UserNotification` rows and publish them to
Redis pub/sub so connected WebSocket clients receive instant updates.

Usage from anywhere in the backend:

    from app.services.in_app_notifications import notify_user, notify_users

    await notify_user(db, redis, user_id=..., type="build_failed", ...)
    await notify_users(db, redis, user_ids=[...], type="agent_offline", ...)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Sequence

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.user import User
from app.models.user_notification import UserNotification


def _user_channel(user_id: uuid.UUID) -> str:
    return f"user:{user_id}:notifications"


async def notify_user(
    db: AsyncSession,
    redis_client: aioredis.Redis,
    *,
    user_id: uuid.UUID,
    type: str,
    title: str,
    body: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> UserNotification:
    """Create a notification for a single user and broadcast via Redis."""
    notif = UserNotification(
        user_id=user_id,
        type=type,
        title=title,
        body=body,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    db.add(notif)
    await db.flush()

    payload = {
        "id": str(notif.id),
        "user_id": str(notif.user_id),
        "type": notif.type,
        "title": notif.title,
        "body": notif.body,
        "entity_type": notif.entity_type,
        "entity_id": str(notif.entity_id) if notif.entity_id else None,
        "read_at": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis_client.publish(_user_channel(user_id), json.dumps(payload))
    return notif


async def notify_users(
    db: AsyncSession,
    redis_client: aioredis.Redis,
    *,
    user_ids: Sequence[uuid.UUID],
    type: str,
    title: str,
    body: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
) -> list[UserNotification]:
    """Create the same notification for multiple users."""
    notifications: list[UserNotification] = []
    seen: set[uuid.UUID] = set()
    for uid in user_ids:
        if uid in seen:
            continue
        seen.add(uid)
        notif = await notify_user(
            db,
            redis_client,
            user_id=uid,
            type=type,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        notifications.append(notif)
    return notifications


async def get_admin_user_ids(db: AsyncSession) -> list[uuid.UUID]:
    """Return IDs of all active admin users."""
    result = await db.execute(
        select(User.id).where(User.is_admin.is_(True), User.is_active.is_(True))
    )
    return list(result.scalars().all())


async def publish_build_update(
    redis_client: aioredis.Redis,
    build,  # app.models.build.Build instance
    *,
    project_id: uuid.UUID | None = None,
) -> None:
    """Publish a ``build_update`` event to the global ``builds:updates`` channel.

    Called whenever a build is created, transitions to a new status, or
    finishes so that the dashboard and builds-list pages can patch their
    local state in real time without polling.

    Pass ``project_id`` explicitly so the WebSocket firehose can filter events
    by project without a DB round-trip per event. If omitted, the payload will
    carry ``null`` for ``project_id`` (events from callers that have not been
    updated yet will be filtered out by scoped clients, not leaked).
    """
    def _iso(val) -> str | None:
        return val.isoformat() if val else None

    payload = {
        "event": "build_update",
        "id": str(build.id),
        "pipeline_id": str(build.pipeline_id),
        "project_id": str(project_id) if project_id else None,
        "number": build.number,
        "branch": build.branch,
        "commit_sha": build.commit_sha,
        "status": build.status,
        "trigger_type": build.trigger_type,
        "started_at": _iso(getattr(build, "started_at", None)),
        "finished_at": _iso(getattr(build, "finished_at", None)),
        "created_at": _iso(getattr(build, "created_at", None)),
        "updated_at": None,  # not a column on Build; kept for API shape compatibility
        "triggered_by": str(build.triggered_by) if build.triggered_by else None,
    }
    await redis_client.publish("builds:updates", json.dumps(payload))

