"""Two-step verification while signing in: the second step, and the forced setup.

Every route here is public and takes a ticket, which the password step hands
out (``app.services.two_step.sign_in``). A ticket is never a session: only a
correct second step, or a finished setup, returns one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record as audit_record
from app.core.rate_limit import rate_limit
from app.database import get_db
from app.models.two_step import TwoStepTicket
from app.models.user import User
from app.schemas.two_step import (
    NamedPasskeyAnswer,
    PasskeyAnswer,
    PasskeyOptions,
    RecoveryCodes,
    SignInResponse,
    TicketCodeRequest,
    TicketRequest,
    TotpSetup,
    VerifyRequest,
)
from app.services.two_step import (
    attempts,
    clock,
    passkeys,
    recovery_codes,
    sign_in,
    status as two_step_status,
    tickets,
    totp,
)

router = APIRouter()

WRONG_CODE = "That code is not right."
WRONG_PASSKEY = "That passkey was not accepted."
TICKET_GONE = "That took too long. Sign in again."

_limit = rate_limit("two_step", max_requests=30, window_seconds=60)


async def audit(db: AsyncSession, action: str, user_id: uuid.UUID, **metadata: Any) -> None:
    await audit_record(
        action=action, actor_id=user_id, target_type="user", target_id=user_id,
        metadata=metadata or None, db=db,
    )


async def _ticket(
    db: AsyncSession, token: str, purpose: str, now: datetime
) -> tuple[TwoStepTicket, User]:
    """The ticket and the account it is for, or 410 when it is no good."""
    ticket = await tickets.find(db, token, purpose, now)
    user = await db.get(User, ticket.user_id) if ticket is not None else None
    if ticket is None or user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=TICKET_GONE)
    return ticket, user


def _paused(pause: attempts.Paused) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Too many wrong tries. Try again in {pause.minutes} "
               f"minute{'' if pause.minutes == 1 else 's'}.",
        headers={"Retry-After": str(pause.seconds)},
    )


async def _guard(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    try:
        await attempts.guard(db, user_id, now)
    except attempts.Paused as pause:
        raise _paused(pause) from None


async def _wrong(
    db: AsyncSession, user_id: uuid.UUID, now: datetime, method: str, detail: str,
    **metadata: Any,
) -> NoReturn:
    """Count a wrong try, record it, and refuse. The count is committed here:
    the error raised next would otherwise roll it back."""
    paused = await attempts.failed(db, user_id, now)
    await audit(db, "two_step_failed", user_id, method=method, **metadata)
    if paused:
        await audit(db, "two_step_paused", user_id)
    await db.commit()
    if paused:
        raise _paused(attempts.Paused(int(attempts.PAUSE.total_seconds())))
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def _signed_in(
    db: AsyncSession, ticket: TwoStepTicket, user: User, method: str, **extra: Any
) -> dict[str, Any]:
    """Use the ticket up and hand out the session."""
    if not await tickets.consume(db, ticket):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=TICKET_GONE)
    await attempts.succeeded(db, user.id)
    await audit(db, "login_success", user.id, second_step=method)
    return {**sign_in.session(user), **extra}


# ── the second step ─────────────────────────────────────────────────────

@router.post("/verify", response_model=SignInResponse, response_model_exclude_none=True)
async def verify(
    body: VerifyRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    """A code from the authenticator app, or a recovery code."""
    now = clock.now()
    ticket, user = await _ticket(db, body.ticket, tickets.VERIFY, now)
    await _guard(db, user.id, now)

    if body.recovery_code is not None:
        if not await recovery_codes.use(db, user.id, body.recovery_code, now):
            await _wrong(db, user.id, now, "recovery_code", WRONG_CODE)
        await audit(db, "two_step_recovery_code_used", user.id)
        left = await recovery_codes.remaining(db, user.id)
        return await _signed_in(db, ticket, user, "recovery_code", recovery_codes_left=left)

    if not await totp.check(db, user.id, body.code or "", now):
        await _wrong(db, user.id, now, "totp", WRONG_CODE)
    return await _signed_in(db, ticket, user, "totp")


@router.post("/passkey/options", response_model=PasskeyOptions, response_model_exclude_none=True)
async def passkey_options(
    body: TicketRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    """A challenge for one of the account's passkeys to sign."""
    now = clock.now()
    ticket, user = await _ticket(db, body.ticket, tickets.VERIFY, now)
    await _guard(db, user.id, now)
    if await passkeys.count(db, user.id) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSKEY)
    options, challenge = await passkeys.authentication_options(db, user.id)
    await tickets.set_challenge(db, ticket, challenge)
    return {"options": options}


@router.post("/passkey/verify", response_model=SignInResponse, response_model_exclude_none=True)
async def passkey_verify(
    body: PasskeyAnswer,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    """The browser's answer to ``/passkey/options``."""
    now = clock.now()
    ticket, user = await _ticket(db, body.ticket, tickets.VERIFY, now)
    await _guard(db, user.id, now)
    challenge = await tickets.take_challenge(db, ticket)
    if challenge is None:
        # No challenge was handed out, or it has been answered already.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=WRONG_PASSKEY)
    try:
        await passkeys.verify(db, user.id, body.credential, challenge, now)
    except passkeys.PasskeyError as refused:
        if refused.counter_went_back:
            await audit(db, "two_step_passkey_counter_went_back", user.id)
        await _wrong(db, user.id, now, "passkey", WRONG_PASSKEY)
    return await _signed_in(db, ticket, user, "passkey")


# ── the forced setup ────────────────────────────────────────────────────

async def _setup_ticket(db: AsyncSession, token: str, now: datetime) -> tuple[TwoStepTicket, User]:
    """A setup ticket that has not added its method yet, for an account that
    still has none. An account that gained a method some other way since the
    ticket was issued must prove it at the second step instead."""
    ticket, user = await _ticket(db, token, tickets.SETUP, now)
    if await two_step_status.is_on(db, user.id):
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=TICKET_GONE)
    return ticket, user


async def _method_added(
    db: AsyncSession, ticket: TwoStepTicket, user: User, method: str, now: datetime
) -> dict[str, Any]:
    await tickets.mark_setup_done(db, ticket)
    codes = await two_step_status.after_method_added(db, user.id, now)
    await audit(db, "two_step_method_added", user.id, method=method, during="sign_in")
    return {"recovery_codes": codes}


@router.post("/setup/totp", response_model=TotpSetup)
async def setup_totp(
    body: TicketRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    _, user = await _setup_ticket(db, body.ticket, clock.now())
    secret, uri = await totp.begin(db, user)
    return {"secret": secret, "otpauth_uri": uri}


@router.post("/setup/totp/confirm", response_model=RecoveryCodes)
async def setup_totp_confirm(
    body: TicketCodeRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    now = clock.now()
    ticket, user = await _setup_ticket(db, body.ticket, now)
    if not await totp.confirm(db, user.id, body.code, now):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_CODE)
    return await _method_added(db, ticket, user, "totp", now)


@router.post("/setup/passkey/options", response_model=PasskeyOptions, response_model_exclude_none=True)
async def setup_passkey_options(
    body: TicketRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    ticket, user = await _setup_ticket(db, body.ticket, clock.now())
    options, challenge = await passkeys.registration_options(db, user)
    await tickets.set_challenge(db, ticket, challenge)
    return {"options": options}


@router.post("/setup/passkey", response_model=RecoveryCodes)
async def setup_passkey(
    body: NamedPasskeyAnswer,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    now = clock.now()
    ticket, user = await _setup_ticket(db, body.ticket, now)
    challenge = await tickets.take_challenge(db, ticket)
    if challenge is None:
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSKEY)
    try:
        await passkeys.register(db, user.id, body.credential, challenge, body.name, now)
    except passkeys.AlreadyRegistered:
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This passkey is already registered."
        ) from None
    except (passkeys.PasskeyError, passkeys.TooManyPasskeys):
        await db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=WRONG_PASSKEY) from None
    return await _method_added(db, ticket, user, "passkey", now)


@router.post("/setup/finish", response_model=SignInResponse, response_model_exclude_none=True)
async def setup_finish(
    body: TicketRequest,
    db: AsyncSession = Depends(get_db),
    _rl: None = Depends(_limit),
) -> dict[str, Any]:
    """The session, for the browser that has just set a method up with this ticket."""
    now = clock.now()
    ticket, user = await _ticket(db, body.ticket, tickets.SETUP_DONE, now)
    if not await two_step_status.is_on(db, user.id):
        # The method was removed again (an administrator's reset) in between.
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=TICKET_GONE)
    return await _signed_in(db, ticket, user, "setup")
