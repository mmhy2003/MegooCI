"""The per-user row of two-step verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import UserTwoStep


def aware(value: datetime | None) -> datetime | None:
    """SQLite (tests) returns naive datetimes; they are stored as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def get_state(db: AsyncSession, user_id: uuid.UUID) -> UserTwoStep | None:
    return await db.get(UserTwoStep, user_id)


async def ensure_state(db: AsyncSession, user_id: uuid.UUID) -> UserTwoStep:
    """The user's row, created if this is the first time it is needed."""
    state = await db.get(UserTwoStep, user_id)
    if state is None:
        state = UserTwoStep(user_id=user_id, failed_attempts=0)
        try:
            # In a savepoint, so that losing the race below undoes this insert
            # and nothing else of the request.
            async with db.begin_nested():
                db.add(state)
                await db.flush()
        except IntegrityError:
            # Another request of the same user created it in the meantime.
            state = await db.get(UserTwoStep, user_id)
    return state
