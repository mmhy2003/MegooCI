"""Codes from an authenticator app (TOTP, RFC 6238).

The kind every app supports: 6 digits, a new one every 30 seconds, SHA-1,
a 160-bit secret. The secret is stored encrypted with the server key and
counts only once a correct code has confirmed it.
"""

from __future__ import annotations

import hmac
import unicodedata
import uuid
from datetime import datetime

import pyotp
from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.two_step import UserTwoStep
from app.models.user import User
from app.services.two_step.state import ensure_state, get_state

ISSUER = "MegooCI"
PERIOD_SECONDS = 30
DIGITS = 6


class AlreadySetUp(Exception):
    """The account has a confirmed authenticator; remove it before adding one."""


def new_secret() -> str:
    return pyotp.random_base32()


def otpauth_uri(secret: str, email: str) -> str:
    """What the QR code holds: the secret, and how the app should label it."""
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=ISSUER)


def _ascii_digits(text: str) -> str | None:
    """*text* without spaces, with digits of any script (an Arabic keyboard
    types ١٢٣) as 0-9. None when it holds anything but digits."""
    out = []
    for char in "".join(text.split()):
        value = unicodedata.decimal(char, None)
        if value is None:
            return None
        out.append(str(value))
    return "".join(out)


def matching_step(secret: str, code: str, at: datetime) -> int | None:
    """The 30-second step *code* is right for at time *at*: the current one,
    or the one before or after it for clocks that are slightly off. None when
    it is right for none of them."""
    digits = _ascii_digits(code)
    if digits is None or len(digits) != DIGITS:
        return None
    totp = pyotp.TOTP(secret)
    current = int(at.timestamp()) // PERIOD_SECONDS
    for step in (current, current - 1, current + 1):
        if hmac.compare_digest(totp.at(step * PERIOD_SECONDS), digits):
            return step
    return None


def is_confirmed(state: UserTwoStep | None) -> bool:
    return (
        state is not None
        and state.totp_secret_encrypted is not None
        and state.totp_confirmed_at is not None
    )


def _secret(state: UserTwoStep) -> str:
    return decrypt_secret(bytes(state.totp_secret_encrypted), get_settings().MEGOOCI_SECRET_KEY)


async def begin(db: AsyncSession, user: User) -> tuple[str, str]:
    """Start setting an authenticator up, or start again. Returns the secret
    and its ``otpauth://`` address. Nothing is on until ``confirm``."""
    state = await ensure_state(db, user.id)
    if is_confirmed(state):
        raise AlreadySetUp()
    secret = new_secret()
    state.totp_secret_encrypted = encrypt_secret(secret, get_settings().MEGOOCI_SECRET_KEY)
    state.totp_confirmed_at = None
    state.totp_last_step = None
    await db.flush()
    return secret, otpauth_uri(secret, user.email)


async def confirm(db: AsyncSession, user_id: uuid.UUID, code: str, now: datetime) -> bool:
    """Turn the authenticator on, given a code that proves the app has the
    secret. False for a wrong code, or when no setup is in progress."""
    state = await get_state(db, user_id)
    if state is None or state.totp_secret_encrypted is None or is_confirmed(state):
        return False
    step = matching_step(_secret(state), code, now)
    if step is None:
        return False
    state.totp_confirmed_at = now
    # The confirming code is spent: it cannot also be used to sign in.
    state.totp_last_step = step
    state.totp_last_used_at = now
    await db.flush()
    return True


async def check(db: AsyncSession, user_id: uuid.UUID, code: str, now: datetime) -> bool:
    """True when *code* is right and has not been used. A code works once:
    its step, and every earlier one, is refused from then on."""
    state = await get_state(db, user_id)
    if not is_confirmed(state):
        return False
    step = matching_step(_secret(state), code, now)
    if step is None:
        return False
    # One statement, so that two requests with the same code cannot both win.
    result = await db.execute(
        update(UserTwoStep)
        .where(
            UserTwoStep.user_id == user_id,
            or_(UserTwoStep.totp_last_step.is_(None), UserTwoStep.totp_last_step < step),
        )
        .values(totp_last_step=step, totp_last_used_at=now)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return False
    await db.refresh(state)
    return True


async def remove(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Take the authenticator off the account. False when it has none."""
    state = await get_state(db, user_id)
    if state is None or state.totp_secret_encrypted is None:
        return False
    state.totp_secret_encrypted = None
    state.totp_confirmed_at = None
    state.totp_last_step = None
    state.totp_last_used_at = None
    await db.flush()
    return True
