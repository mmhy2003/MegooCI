"""Passkeys (WebAuthn) as the second step.

The relying party is the host of ``MEGOOCI_PUBLIC_URL`` and the only accepted
origin is that URL's origin, so a passkey works only when the app is opened
at that address. A passkey comes after the password, so it need not verify
the user itself (a plain security key is enough), and no attestation is asked.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import get_settings
from app.models.two_step import UserPasskey
from app.models.user import User

RP_NAME = "MegooCI"
MAX_PASSKEYS = 10
MAX_NAME_LENGTH = 100
DEFAULT_NAME = "Passkey"
_KNOWN_TRANSPORTS = {"usb", "nfc", "ble", "smart-card", "hybrid", "internal", "cable"}
_DEFAULT_PORTS = {"http": 80, "https": 443}


class PasskeyError(Exception):
    """The browser's answer was not accepted. ``counter_went_back`` marks the
    one case worth recording: the sign of a copied key."""

    def __init__(self, message: str = "not accepted", *, counter_went_back: bool = False) -> None:
        super().__init__(message)
        self.counter_went_back = counter_went_back


class TooManyPasskeys(Exception):
    """The account already has ``MAX_PASSKEYS``."""


class AlreadyRegistered(Exception):
    """This passkey is already registered."""


def origin() -> str:
    """The one origin passkeys are accepted from, as a browser reports it."""
    url = urlsplit(get_settings().MEGOOCI_PUBLIC_URL)
    scheme = (url.scheme or "http").lower()
    host = (url.hostname or "localhost").lower()
    if url.port is None or url.port == _DEFAULT_PORTS.get(scheme):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{url.port}"


def rp_id() -> str:
    return (urlsplit(get_settings().MEGOOCI_PUBLIC_URL).hostname or "localhost").lower()


def clean_name(name: str | None) -> str:
    text = " ".join((name or "").split())
    return text[:MAX_NAME_LENGTH] or DEFAULT_NAME


async def list_for(db: AsyncSession, user_id: uuid.UUID) -> list[UserPasskey]:
    result = await db.execute(
        select(UserPasskey)
        .where(UserPasskey.user_id == user_id)
        .order_by(UserPasskey.created_at, UserPasskey.id)
    )
    return list(result.scalars())


async def count(db: AsyncSession, user_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(UserPasskey).where(UserPasskey.user_id == user_id)
        )
        or 0
    )


def _descriptors(passkeys: list[UserPasskey]) -> list[PublicKeyCredentialDescriptor]:
    return [PublicKeyCredentialDescriptor(id=bytes(p.credential_id)) for p in passkeys]


async def registration_options(db: AsyncSession, user: User) -> tuple[dict[str, Any], bytes]:
    """What the browser needs to create a passkey for *user*, and the
    challenge to keep until its answer comes back."""
    existing = await list_for(db, user.id)
    if len(existing) >= MAX_PASSKEYS:
        raise TooManyPasskeys()
    options = generate_registration_options(
        rp_id=rp_id(),
        rp_name=RP_NAME,
        user_id=user.id.bytes,
        user_name=user.email,
        user_display_name=user.name,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        # A passkey already on the account cannot be registered again.
        exclude_credentials=_descriptors(existing),
    )
    return json.loads(options_to_json(options)), options.challenge


async def register(
    db: AsyncSession,
    user_id: uuid.UUID,
    credential: dict[str, Any],
    challenge: bytes,
    name: str | None,
    now: datetime,
) -> UserPasskey:
    """Check the browser's answer to ``registration_options`` and store the
    passkey. Raises ``PasskeyError`` when the answer is not acceptable."""
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id(),
            expected_origin=origin(),
        )
    except Exception as exc:  # the library raises many kinds for a bad answer
        raise PasskeyError() from exc

    taken = await db.scalar(
        select(UserPasskey.id).where(UserPasskey.credential_id == verified.credential_id)
    )
    if taken is not None:
        raise AlreadyRegistered()
    if await count(db, user_id) >= MAX_PASSKEYS:
        raise TooManyPasskeys()

    response = credential.get("response")
    reported = response.get("transports") if isinstance(response, dict) else None
    transports = [t for t in reported or [] if isinstance(t, str) and t in _KNOWN_TRANSPORTS]
    passkey = UserPasskey(
        user_id=user_id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=transports,
        name=clean_name(name),
        created_at=now,
    )
    db.add(passkey)
    await db.flush()
    return passkey


async def authentication_options(
    db: AsyncSession, user_id: uuid.UUID
) -> tuple[dict[str, Any], bytes]:
    """What the browser needs to sign in with one of the account's passkeys,
    and the challenge to keep."""
    options = generate_authentication_options(
        rp_id=rp_id(),
        allow_credentials=_descriptors(await list_for(db, user_id)),
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    return json.loads(options_to_json(options)), options.challenge


async def verify(
    db: AsyncSession,
    user_id: uuid.UUID,
    credential: dict[str, Any],
    challenge: bytes,
    now: datetime,
) -> UserPasskey:
    """Check the browser's answer to ``authentication_options``. Returns the
    passkey that signed it; raises ``PasskeyError`` otherwise."""
    try:
        credential_id = base64url_to_bytes(str(credential["rawId"]))
    except Exception as exc:
        raise PasskeyError() from exc

    passkey = (
        await db.execute(
            select(UserPasskey).where(
                UserPasskey.user_id == user_id, UserPasskey.credential_id == credential_id
            )
        )
    ).scalar_one_or_none()
    if passkey is None:
        raise PasskeyError()

    try:
        # The counter is compared below, where a copied key can be told apart
        # from an answer that is simply wrong.
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id(),
            expected_origin=origin(),
            credential_public_key=bytes(passkey.public_key),
            credential_current_sign_count=0,
        )
    except Exception as exc:
        raise PasskeyError() from exc

    stored = passkey.sign_count or 0
    reported = verified.new_sign_count
    # Authenticators that do not count always report 0. One that counts must
    # report more than last time; if not, the key has been copied.
    if (reported > 0 or stored > 0) and reported <= stored:
        raise PasskeyError(counter_went_back=True)

    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = now
    await db.flush()
    return passkey


async def rename(
    db: AsyncSession, user_id: uuid.UUID, passkey_id: uuid.UUID, name: str
) -> UserPasskey | None:
    passkey = await db.get(UserPasskey, passkey_id)
    if passkey is None or passkey.user_id != user_id:
        return None
    passkey.name = clean_name(name)
    await db.flush()
    return passkey


async def remove(db: AsyncSession, user_id: uuid.UUID, passkey_id: uuid.UUID) -> bool:
    passkey = await db.get(UserPasskey, passkey_id)
    if passkey is None or passkey.user_id != user_id:
        return False
    await db.delete(passkey)
    await db.flush()
    return True
