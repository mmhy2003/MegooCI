"""In-app user notification endpoints.

Authenticated users can list their own notifications, mark individual ones
as read, mark all as read, and query their unread count.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_active_user
from app.database import get_db
from app.models.user import User
from app.models.user_notification import UserNotification
from app.schemas.user_notification import UnreadCountResponse, UserNotificationResponse

router = APIRouter()


@router.get("", response_model=list[UserNotificationResponse])
async def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(30, ge=1, le=100),
    before: uuid.UUID | None = Query(None, description="Cursor: notification id to paginate before"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> list[UserNotificationResponse]:
    q = (
        select(UserNotification)
        .where(UserNotification.user_id == current_user.id)
        .order_by(UserNotification.created_at.desc())
        .limit(limit)
    )

    if unread_only:
        q = q.where(UserNotification.read_at.is_(None))

    if before:
        cursor_row = await db.get(UserNotification, before)
        if cursor_row and cursor_row.user_id == current_user.id:
            q = q.where(UserNotification.created_at < cursor_row.created_at)

    result = await db.execute(q)
    return [
        UserNotificationResponse.model_validate(n) for n in result.scalars().all()
    ]


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> UnreadCountResponse:
    result = await db.execute(
        select(func.count())
        .select_from(UserNotification)
        .where(
            UserNotification.user_id == current_user.id,
            UserNotification.read_at.is_(None),
        )
    )
    return UnreadCountResponse(count=result.scalar_one())


@router.patch("/{notification_id}/read", response_model=UserNotificationResponse)
async def mark_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> UserNotificationResponse:
    notif = await db.get(UserNotification, notification_id)
    if notif is None or notif.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )
    if notif.read_at is None:
        notif.read_at = datetime.now(timezone.utc)
    await db.flush()
    return UserNotificationResponse.model_validate(notif)


@router.post("/mark-all-read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> None:
    await db.execute(
        update(UserNotification)
        .where(
            UserNotification.user_id == current_user.id,
            UserNotification.read_at.is_(None),
        )
        .values(read_at=datetime.now(timezone.utc))
    )
