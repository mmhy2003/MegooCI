"""What an account has, and whether the server requires it.

Two-step verification is *on* for an account that has at least one method: a
confirmed authenticator app, or a passkey. It is *required* when the
administrator's switch (the ``require_two_step`` system setting) is on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting
from app.models.two_step import UserPasskey, UserTwoStep
from app.models.user import User
from app.services.two_step import passkeys, recovery_codes, totp
from app.services.two_step.state import aware, get_state

REQUIRE_KEY = "require_two_step"
TOTP = "totp"
PASSKEY = "passkey"


async def methods(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """The kinds of method the account has, authenticator first."""
    kinds = []
    if totp.is_confirmed(await get_state(db, user_id)):
        kinds.append(TOTP)
    if await passkeys.count(db, user_id) > 0:
        kinds.append(PASSKEY)
    return kinds


async def is_on(db: AsyncSession, user_id: uuid.UUID) -> bool:
    return bool(await methods(db, user_id))


async def is_required(db: AsyncSession) -> bool:
    row = await db.get(SystemSetting, REQUIRE_KEY)
    return row is not None and row.value == "true"


async def set_required(db: AsyncSession, required: bool) -> None:
    row = await db.get(SystemSetting, REQUIRE_KEY)
    value = "true" if required else "false"
    if row is None:
        db.add(SystemSetting(key=REQUIRE_KEY, value=value))
    else:
        row.value = value
    await db.flush()


async def setup_required(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """True when the account must set two-step verification up and has not."""
    return await is_required(db) and not await is_on(db, user_id)


async def enabled_user_ids(db: AsyncSession) -> set[uuid.UUID]:
    """Every account that has two-step verification on."""
    with_totp = await db.execute(
        select(UserTwoStep.user_id).where(
            UserTwoStep.totp_secret_encrypted.is_not(None),
            UserTwoStep.totp_confirmed_at.is_not(None),
        )
    )
    with_passkey = await db.execute(select(UserPasskey.user_id).distinct())
    return set(with_totp.scalars()) | set(with_passkey.scalars())


async def counts(db: AsyncSession) -> tuple[int, int]:
    """How many active users there are, and how many of them have it on."""
    active = set(
        (await db.execute(select(User.id).where(User.is_active.is_(True)))).scalars()
    )
    return len(active), len(active & await enabled_user_ids(db))


async def after_method_added(
    db: AsyncSession, user_id: uuid.UUID, now: datetime
) -> list[str] | None:
    """Call when a method has been added. Returns ten new recovery codes when
    it was the account's first, and None when the account already has codes."""
    state = await get_state(db, user_id)
    if state is not None and state.recovery_codes_generated_at is not None:
        return None
    return await recovery_codes.generate(db, user_id, now)


async def after_method_removed(db: AsyncSession, user_id: uuid.UUID) -> None:
    """Call when a method has been removed. Without any method left, the
    recovery codes go too: they stand in for a method the account no longer has."""
    if not await is_on(db, user_id):
        await recovery_codes.clear(db, user_id)


async def summary(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Everything the Settings tab shows. Never a secret, a hash or a code."""
    state = await get_state(db, user_id)
    keys = await passkeys.list_for(db, user_id)
    confirmed = totp.is_confirmed(state)
    enabled = confirmed or bool(keys)
    return {
        "enabled": enabled,
        "required": await is_required(db),
        "totp": (
            {
                "confirmed_at": aware(state.totp_confirmed_at),
                "last_used_at": aware(state.totp_last_used_at),
            }
            if confirmed
            else None
        ),
        "passkeys": [
            {
                "id": key.id,
                "name": key.name,
                "created_at": aware(key.created_at),
                "last_used_at": aware(key.last_used_at),
            }
            for key in keys
        ],
        "recovery_codes": (
            {
                "left": await recovery_codes.remaining(db, user_id),
                "generated_at": aware(state.recovery_codes_generated_at),
            }
            if state is not None and state.recovery_codes_generated_at is not None
            else None
        ),
        "passkey_origin": passkeys.origin(),
    }
