"""Remove everything an account has for two-step verification.

For a user who has lost the phone, the passkeys and the recovery codes: an
administrator resets them, and they set it up again.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.services.two_step import status


async def reset(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Remove the account's authenticator, passkeys, recovery codes, pending
    tickets and any pause from wrong tries. Returns whether two-step
    verification was on. Does not end the user's sessions."""
    was_on = await status.is_on(db, user_id)
    for model in (UserPasskey, UserRecoveryCode, TwoStepTicket, UserTwoStep):
        await db.execute(
            delete(model)
            .where(model.user_id == user_id)
            .execution_options(synchronize_session="fetch")
        )
    await db.flush()
    return was_on
