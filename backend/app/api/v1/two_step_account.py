"""Two-step verification for one's own account: the Settings tab.

Every route needs a browser session. An API token cannot add or remove a
method, whatever its scope: a leaked token must not be able to weaken, or
take over, the way its owner signs in.

A session alone is not enough either. Adding a method, removing one and
replacing the recovery codes each ask for the password, so a session that has
fallen into the wrong hands cannot put its holder's own second step on the
account.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.two_step import WRONG_CODE, WRONG_PASSKEY, audit
from app.core.deps import get_current_active_user
from app.core.rate_limit import rate_limit
from app.core.security import verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.two_step import (
    AddPasskeyRequest,
    ConfirmTotpRequest,
    PasskeyAdded,
    PasskeyInfo,
    PasskeyOptions,
    PasswordRequest,
    RecoveryCodes,
    RenameRequest,
    TotpSetup,
    TwoStepSummary,
)
from app.services.two_step import (
    clock,
    passkeys,
    recovery_codes,
    status as two_step_status,
    tickets,
    totp,
)
from app.services.two_step.state import get_state

router = APIRouter()

REQUIRED = "Two-step verification is required on this server."

_password_limit = rate_limit("two_step_password", max_requests=10, window_seconds=60)


async def session_user(current_user: User = Depends(get_current_active_user)) -> User:
    """The signed-in user, when they are signed in with a browser session."""
    if getattr(current_user, "authenticated_with_token", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Two-step verification cannot be managed with an API token.",
        )
    return current_user


def _check_password(user: User, password: str) -> None:
    if not user.hashed_password or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Wrong password.")


async def _refuse_if_last_and_required(db: AsyncSession, methods_left: bool) -> None:
    if not methods_left and await two_step_status.is_required(db):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=REQUIRED)


def _passkey_info(passkey) -> dict[str, Any]:
    return {
        "id": passkey.id,
        "name": passkey.name,
        "created_at": passkey.created_at,
        "last_used_at": passkey.last_used_at,
    }


@router.get("", response_model=TwoStepSummary)
async def get_two_step(
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    return await two_step_status.summary(db, user.id)


# ── authenticator app ───────────────────────────────────────────────────

@router.post("/totp", response_model=TotpSetup)
async def begin_totp(
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """A new secret to scan. Nothing is on until ``/totp/confirm``."""
    try:
        secret, uri = await totp.begin(db, user)
    except totp.AlreadySetUp:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An authenticator app is already set up. Remove it first.",
        ) from None
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/totp/confirm", response_model=RecoveryCodes)
async def confirm_totp(
    body: ConfirmTotpRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_password_limit),
) -> dict[str, Any]:
    _check_password(user, body.password)
    now = clock.now()
    # Not counted towards the pause: the secret being tested is the user's own.
    if not await totp.confirm(db, user.id, body.code, now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_CODE)
    codes = await two_step_status.after_method_added(db, user.id, now)
    await audit(db, "two_step_method_added", user.id, method="totp")
    return {"recovery_codes": codes}


@router.post("/totp/remove", response_model=TwoStepSummary)
async def remove_totp(
    body: PasswordRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_password_limit),
) -> dict[str, Any]:
    _check_password(user, body.password)
    if not totp.is_confirmed(await get_state(db, user.id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No authenticator app is set up."
        )
    await _refuse_if_last_and_required(db, methods_left=await passkeys.count(db, user.id) > 0)
    await totp.remove(db, user.id)
    await two_step_status.after_method_removed(db, user.id)
    await audit(db, "two_step_method_removed", user.id, method="totp")
    return await two_step_status.summary(db, user.id)


# ── passkeys ────────────────────────────────────────────────────────────

@router.post("/passkeys/options", response_model=PasskeyOptions)
async def passkey_registration_options(
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """What the browser needs to create a passkey, and a ticket to send back
    with its answer."""
    try:
        options, challenge = await passkeys.registration_options(db, user)
    except passkeys.TooManyPasskeys:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account can have {passkeys.MAX_PASSKEYS} passkeys at most.",
        ) from None
    now = clock.now()
    token = await tickets.issue(db, user.id, tickets.REGISTER, now)
    ticket = await tickets.find(db, token, tickets.REGISTER, now)
    await tickets.set_challenge(db, ticket, challenge)
    return {"options": options, "ticket": token}


@router.post("/passkeys", response_model=PasskeyAdded)
async def add_passkey(
    body: AddPasskeyRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_password_limit),
) -> dict[str, Any]:
    # Before the ticket is touched: a mistyped password must not waste the
    # passkey the browser has just created.
    _check_password(user, body.password)
    now = clock.now()
    ticket = await tickets.find(db, body.ticket, tickets.REGISTER, now)
    challenge = (
        await tickets.take_challenge(db, ticket)
        if ticket is not None and ticket.user_id == user.id
        else None
    )
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSKEY)
    await tickets.consume(db, ticket)
    try:
        passkey = await passkeys.register(db, user.id, body.credential, challenge, body.name, now)
    except passkeys.AlreadyRegistered:
        await db.commit()  # the challenge stays spent
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This passkey is already registered."
        ) from None
    except passkeys.TooManyPasskeys:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"An account can have {passkeys.MAX_PASSKEYS} passkeys at most.",
        ) from None
    except passkeys.PasskeyError:
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSKEY) from None
    codes = await two_step_status.after_method_added(db, user.id, now)
    await audit(db, "two_step_method_added", user.id, method="passkey")
    return {"passkey": _passkey_info(passkey), "recovery_codes": codes}


@router.patch("/passkeys/{passkey_id}", response_model=PasskeyInfo)
async def rename_passkey(
    passkey_id: uuid.UUID,
    body: RenameRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    passkey = await passkeys.rename(db, user.id, passkey_id, body.name)
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found.")
    return _passkey_info(passkey)


@router.post("/passkeys/{passkey_id}/remove", response_model=TwoStepSummary)
async def remove_passkey(
    passkey_id: uuid.UUID,
    body: PasswordRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_password_limit),
) -> dict[str, Any]:
    _check_password(user, body.password)
    mine = await passkeys.list_for(db, user.id)
    if passkey_id not in {passkey.id for passkey in mine}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey not found.")
    has_totp = totp.is_confirmed(await get_state(db, user.id))
    await _refuse_if_last_and_required(db, methods_left=has_totp or len(mine) > 1)
    await passkeys.remove(db, user.id, passkey_id)
    await two_step_status.after_method_removed(db, user.id)
    await audit(db, "two_step_method_removed", user.id, method="passkey")
    return await two_step_status.summary(db, user.id)


# ── recovery codes ──────────────────────────────────────────────────────

@router.post("/recovery-codes", response_model=RecoveryCodes)
async def regenerate_recovery_codes(
    body: PasswordRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_password_limit),
) -> dict[str, Any]:
    """Ten new codes. The old ones stop working."""
    _check_password(user, body.password)
    if not await two_step_status.is_on(db, user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Turn two-step verification on first.",
        )
    codes = await recovery_codes.generate(db, user.id, clock.now())
    await audit(db, "two_step_recovery_codes_generated", user.id)
    return {"recovery_codes": codes}
