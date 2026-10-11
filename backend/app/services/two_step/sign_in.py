"""What a user gets once their password has been accepted.

One place decides, for login, sign-up and an accepted invitation alike:

- ``signed_in``                the session, for an account without two-step
                               verification on a server that does not require it
- ``two_step_required``        a ticket for the second step
- ``two_step_setup_required``  a ticket for the forced setup
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token
from app.models.user import User
from app.services.two_step import passkeys, status, tickets

SIGNED_IN = "signed_in"
TWO_STEP_REQUIRED = "two_step_required"
SETUP_REQUIRED = "two_step_setup_required"


def session(user: User) -> dict[str, Any]:
    """The tokens of a signed-in session. Only call this for a user who has
    proved everything their account asks for."""
    token_data = {"sub": str(user.id)}
    return {
        "status": SIGNED_IN,
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


async def decide(db: AsyncSession, user: User, now: datetime) -> dict[str, Any]:
    """The answer to a correct password."""
    kinds = await status.methods(db, user.id)
    if kinds:
        return {
            "status": TWO_STEP_REQUIRED,
            "ticket": await tickets.issue(db, user.id, tickets.VERIFY, now),
            "methods": kinds,
            "passkey_origin": passkeys.origin(),
        }
    if await status.is_required(db):
        return {
            "status": SETUP_REQUIRED,
            "ticket": await tickets.issue(db, user.id, tickets.SETUP, now),
            "passkey_origin": passkeys.origin(),
        }
    return session(user)


async def may_renew(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Whether a session may renew itself. Not when two-step verification is
    required and the account still has none: a session renews every time it
    is used, so it would otherwise never have to be set up."""
    return not await status.setup_required(db, user_id)
