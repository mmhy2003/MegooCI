"""One-time recovery codes, for when the phone and the passkeys are gone.

Ten at a time, shown once. They are stored hashed with bcrypt, so a copy of
the database does not give them away, and each works once.
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
from datetime import datetime

import bcrypt
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import UserRecoveryCode
from app.services.two_step.state import ensure_state, get_state

COUNT = 10
LENGTH = 10
# Letters and digits without the ones that look alike (0/o, 1/l/i).
ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
# A code is about 50 random bits, so a moderate cost is far more than enough,
# and ten of them are hashed (or compared) in one go.
HASH_ROUNDS = 10


def new_code() -> str:
    """A fresh code, as it is shown: ``xxxxx-xxxxx``."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(LENGTH))
    return f"{raw[:5]}-{raw[5:]}"


def normalise(text: str) -> str | None:
    """*text* as a bare code: no dash, no spaces, lower case. None when it
    cannot be a code at all."""
    raw = "".join(text.split()).replace("-", "").lower()
    if len(raw) != LENGTH or any(char not in ALPHABET for char in raw):
        return None
    return raw


def _hash(raw: str) -> str:
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt(rounds=HASH_ROUNDS)).decode()


def _matching(raw: str, candidates: list[tuple[uuid.UUID, str]]) -> uuid.UUID | None:
    for code_id, code_hash in candidates:
        try:
            if bcrypt.checkpw(raw.encode(), code_hash.encode()):
                return code_id
        except ValueError:
            continue
    return None


async def generate(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> list[str]:
    """Replace the account's codes with ten new ones and return them. This is
    the only time they exist in the clear."""
    codes = [new_code() for _ in range(COUNT)]
    # Hashing is slow on purpose: keep it off the event loop.
    hashes = await asyncio.to_thread(lambda: [_hash(normalise(code)) for code in codes])
    state = await ensure_state(db, user_id)
    await db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    db.add_all(
        UserRecoveryCode(user_id=user_id, code_hash=code_hash, created_at=now)
        for code_hash in hashes
    )
    state.recovery_codes_generated_at = now
    await db.flush()
    return codes


async def use(db: AsyncSession, user_id: uuid.UUID, code: str, now: datetime) -> bool:
    """True when *code* is one of the account's unused codes, which is then
    used up."""
    raw = normalise(code)
    if raw is None:
        return False
    unused = (
        await db.execute(
            select(UserRecoveryCode.id, UserRecoveryCode.code_hash).where(
                UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None)
            )
        )
    ).all()
    code_id = await asyncio.to_thread(_matching, raw, [(row.id, row.code_hash) for row in unused])
    if code_id is None:
        return False
    # One statement, so that two requests with the same code cannot both win.
    result = await db.execute(
        update(UserRecoveryCode)
        .where(UserRecoveryCode.id == code_id, UserRecoveryCode.used_at.is_(None))
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def remaining(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        )
        or 0
    )


async def clear(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Delete every code of the account, used or not."""
    await db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    state = await get_state(db, user_id)
    if state is not None:
        state.recovery_codes_generated_at = None
    await db.flush()
