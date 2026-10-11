"""Wrong tries at the second step, and the pause after too many.

Kept in the database and not in Redis: the request rate limiter lets every
request through when Redis cannot be reached. Reaching the second step at all
needs the account's password, so this cannot lock someone else out.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import UserTwoStep
from app.services.two_step.state import aware, ensure_state

MAX_FAILURES = 5
PAUSE = timedelta(minutes=5)


class Paused(Exception):
    """Too many wrong tries: nothing is checked until the pause is over."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"paused for {seconds}s")
        self.seconds = seconds

    @property
    def minutes(self) -> int:
        return max(1, math.ceil(self.seconds / 60))


async def guard(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """Call before checking an answer. Raises ``Paused`` during a pause.

    Takes the account's row lock for the rest of the transaction, so tries
    for one account are checked one at a time and the count is exact.
    """
    await ensure_state(db, user_id)
    state = (
        await db.execute(
            select(UserTwoStep)
            .where(UserTwoStep.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    until = aware(state.locked_until)
    if until is None:
        return
    if until > now:
        raise Paused(math.ceil((until - now).total_seconds()))
    # The pause is over: start counting again.
    state.locked_until = None
    state.failed_attempts = 0
    await db.flush()


async def failed(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> bool:
    """Count a wrong try. True when it was one too many and a pause begins."""
    state = await ensure_state(db, user_id)
    state.failed_attempts = (state.failed_attempts or 0) + 1
    paused = state.failed_attempts >= MAX_FAILURES
    if paused:
        state.locked_until = now + PAUSE
    await db.flush()
    return paused


async def succeeded(db: AsyncSession, user_id: uuid.UUID) -> None:
    """A right answer: forget the wrong ones."""
    state = await ensure_state(db, user_id)
    state.failed_attempts = 0
    state.locked_until = None
    await db.flush()
