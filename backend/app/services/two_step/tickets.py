"""Tickets: short-lived proof that a browser gave the right password.

A ticket is good for one purpose and a few minutes, and is never a session.
The browser holds a random token; the server stores only its SHA-256.

- ``verify``    the second step of signing in
- ``setup``     the forced setup, for an account that must have a method;
                once it has added its one method it becomes ``setup_done``,
                which opens nothing but the session
- ``register``  adding a passkey from Settings (carries the challenge)
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import TwoStepTicket
from app.services.two_step.state import aware

VERIFY = "verify"
SETUP = "setup"
REGISTER = "register"
# What a setup ticket turns into; never issued directly.
SETUP_DONE = "setup_done"

LIFETIME = {
    VERIFY: timedelta(minutes=5),
    SETUP: timedelta(minutes=15),
    REGISTER: timedelta(minutes=5),
}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def issue(db: AsyncSession, user_id: uuid.UUID, purpose: str, now: datetime) -> str:
    """A new ticket for *user_id*. Returns the token to hand to the browser."""
    # Nothing else removes tickets that were never used.
    await db.execute(
        delete(TwoStepTicket)
        .where(TwoStepTicket.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    token = secrets.token_urlsafe(32)
    db.add(
        TwoStepTicket(
            user_id=user_id,
            purpose=purpose,
            token_hash=_hash(token),
            expires_at=now + LIFETIME[purpose],
            created_at=now,
        )
    )
    await db.flush()
    return token


async def find(db: AsyncSession, token: str, purpose: str, now: datetime) -> TwoStepTicket | None:
    """The ticket *token* stands for, if it is for *purpose* and still valid."""
    if not token:
        return None
    ticket = (
        await db.execute(
            select(TwoStepTicket).where(
                TwoStepTicket.token_hash == _hash(token), TwoStepTicket.purpose == purpose
            )
        )
    ).scalar_one_or_none()
    if ticket is None or aware(ticket.expires_at) <= now:
        return None
    return ticket


async def set_challenge(db: AsyncSession, ticket: TwoStepTicket, challenge: bytes) -> None:
    """Remember the passkey challenge handed out with this ticket. A new one
    replaces the last."""
    ticket.webauthn_challenge = challenge
    await db.flush()


async def take_challenge(db: AsyncSession, ticket: TwoStepTicket) -> bytes | None:
    """The ticket's passkey challenge, which is then gone: a challenge is good
    for one answer, right or wrong."""
    challenge = ticket.webauthn_challenge
    if challenge is None:
        return None
    result = await db.execute(
        update(TwoStepTicket)
        .where(TwoStepTicket.id == ticket.id, TwoStepTicket.webauthn_challenge.is_not(None))
        .values(webauthn_challenge=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    await db.refresh(ticket)
    return bytes(challenge)


async def mark_setup_done(db: AsyncSession, ticket: TwoStepTicket) -> None:
    """The setup ticket has added its method: from now on it can only be
    exchanged for the session."""
    ticket.purpose = SETUP_DONE
    await db.flush()


async def consume(db: AsyncSession, ticket: TwoStepTicket) -> bool:
    """Use the ticket up. False when another request got there first."""
    result = await db.execute(
        delete(TwoStepTicket)
        .where(TwoStepTicket.id == ticket.id)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1
