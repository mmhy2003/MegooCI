"""Notification channel management (admin-only).

CRUD for notification channels (email, Slack, Telegram) plus a test endpoint
and a read-only delivery audit log.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import require_permission
from app.database import get_db
from app.models.notification import NotificationChannel, NotificationDelivery
from app.models.user import User
from app.schemas.notification import (
    NotificationChannelCreate,
    NotificationChannelResponse,
    NotificationChannelTestResult,
    NotificationChannelUpdate,
    NotificationDeliveryResponse,
    redact_config,
)
from app.services.notification_service import (
    decrypt_channel_config,
    encrypt_channel_config,
    test_channel,
    validate_channel_config,
)

router = APIRouter()


def _channel_to_response(channel: NotificationChannel) -> NotificationChannelResponse:
    config = decrypt_channel_config(channel.config_encrypted)
    return NotificationChannelResponse(
        id=channel.id,
        name=channel.name,
        channel_type=channel.channel_type,
        enabled=channel.enabled,
        config_summary=redact_config(config),
        validation_status=channel.validation_status,
        last_validated_at=channel.last_validated_at,
        validation_error=channel.validation_error,
        created_by=channel.created_by,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


@router.get("/channels", response_model=list[NotificationChannelResponse])
async def list_channels(
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("admin")),
) -> list[NotificationChannelResponse]:
    result = await db.execute(
        select(NotificationChannel).order_by(NotificationChannel.created_at.desc())
    )
    channels = result.scalars().all()
    return [_channel_to_response(c) for c in channels]


@router.post(
    "/channels",
    response_model=NotificationChannelResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_channel(
    body: NotificationChannelCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("admin")),
) -> NotificationChannelResponse:
    errors = validate_channel_config(body.channel_type, body.config)
    if errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid config: {'; '.join(errors)}",
        )

    channel = NotificationChannel(
        name=body.name,
        channel_type=body.channel_type,
        config_encrypted=encrypt_channel_config(body.config),
        enabled=True,
        validation_status="unknown",
        created_by=current_user.id,
    )
    db.add(channel)

    try:
        await db.flush()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Could not create channel: {exc}",
        )

    await db.commit()
    await db.refresh(channel)
    return _channel_to_response(channel)


@router.get("/channels/{channel_id}", response_model=NotificationChannelResponse)
async def get_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("admin")),
) -> NotificationChannelResponse:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    return _channel_to_response(channel)


@router.patch("/channels/{channel_id}", response_model=NotificationChannelResponse)
async def update_channel(
    channel_id: uuid.UUID,
    body: NotificationChannelUpdate,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("admin")),
) -> NotificationChannelResponse:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )

    if body.name is not None:
        channel.name = body.name
    if body.enabled is not None:
        channel.enabled = body.enabled

    if body.config is not None:
        existing_config = decrypt_channel_config(channel.config_encrypted)
        merged = {**existing_config, **body.config}
        errors = validate_channel_config(channel.channel_type, merged)
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid config: {'; '.join(errors)}",
            )
        channel.config_encrypted = encrypt_channel_config(merged)
        channel.validation_status = "unknown"
        channel.last_validated_at = None
        channel.validation_error = None

    await db.commit()
    await db.refresh(channel)
    return _channel_to_response(channel)


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("admin")),
) -> None:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    await db.delete(channel)
    await db.commit()


@router.post(
    "/channels/{channel_id}/test",
    response_model=NotificationChannelTestResult,
)
async def test_channel_endpoint(
    channel_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("admin")),
) -> NotificationChannelTestResult:
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )

    ok, detail = await test_channel(db, channel_id)

    channel.validation_status = "ok" if ok else "failed"
    channel.last_validated_at = datetime.now(timezone.utc)
    channel.validation_error = None if ok else detail[:2000]
    await db.commit()

    return NotificationChannelTestResult(ok=ok, detail=detail)


@router.get("/deliveries", response_model=list[NotificationDeliveryResponse])
async def list_deliveries(
    channel_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("admin")),
) -> list[NotificationDeliveryResponse]:
    q = select(NotificationDelivery).order_by(
        NotificationDelivery.created_at.desc()
    ).limit(limit)
    if channel_id:
        q = q.where(NotificationDelivery.channel_id == channel_id)
    result = await db.execute(q)
    return [
        NotificationDeliveryResponse.model_validate(d)
        for d in result.scalars().all()
    ]
