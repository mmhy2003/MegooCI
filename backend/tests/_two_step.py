"""Helpers for tests of two-step verification: accounts, and turning it on."""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import pyotp

from app.models.user import User
from app.services.two_step import passkeys, status, totp
from tests._passkey import SoftwareAuthenticator

# The middle of a 30-second step, so "30 seconds later" is exactly the next one.
NOW = datetime(2026, 10, 11, 12, 0, 15, tzinfo=timezone.utc)
STEP = timedelta(seconds=30)
PASSWORD = "correct horse"


def cheap_hash(password: str) -> str:
    """A real bcrypt hash at the lowest cost: ``verify_password`` accepts it,
    and a test that makes many accounts stays fast."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=4)).decode()


def code_at(secret: str, when: datetime) -> str:
    """The code an authenticator app shows for *secret* at *when*."""
    return pyotp.TOTP(secret).at(when)


async def add_user(db, *, password: str | None = PASSWORD, is_admin: bool = False,
                   is_active: bool = True, name: str = "Sara Ahmed") -> User:
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:12]}@example.com", name=name,
        hashed_password=cheap_hash(password) if password else None,
        is_admin=is_admin, is_active=is_active,
    )
    user.user_roles = []
    db.add(user)
    await db.flush()
    return user


async def turn_on_totp(db, user: User, now: datetime = NOW) -> tuple[str, list[str] | None]:
    """Give *user* a confirmed authenticator. Returns its secret, and the
    recovery codes when this was the account's first method."""
    secret, _ = await totp.begin(db, user)
    earlier = now - 20 * STEP  # leaves every later code unused
    assert await totp.confirm(db, user.id, code_at(secret, earlier), earlier)
    return secret, await status.after_method_added(db, user.id, now)


async def add_passkey(db, user: User, now: datetime = NOW, *,
                      name: str = "Laptop") -> tuple[SoftwareAuthenticator, list[str] | None]:
    """Register a passkey for *user*. Returns the device that holds it, and
    the recovery codes when this was the account's first method."""
    device = SoftwareAuthenticator()
    options, challenge = await passkeys.registration_options(db, user)
    await passkeys.register(db, user.id, device.create(options), challenge, name, now)
    return device, await status.after_method_added(db, user.id, now)
