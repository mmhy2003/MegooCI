"""Anything that changes how an account signs in asks for its password: adding
a method, and changing the email a password reset would go to. A session that
has fallen into the wrong hands must not be enough to take the account over."""
import os
import sys
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import auth, two_step_account as account
from app.core.deps import get_current_user
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import UpdateProfileRequest
from app.schemas.two_step import AddPasskeyRequest, ConfirmTotpRequest, PasswordRequest
from app.services.two_step import passkeys, status
from tests._passkey import SoftwareAuthenticator
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, PASSWORD, add_user, code_at, turn_on_totp


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr("app.core.audit.async_session", factory)
    monkeypatch.setattr("app.services.two_step.clock.now", lambda: NOW)
    yield factory
    await engine.dispose()


async def _account(sf, **kwargs) -> User:
    async with sf() as db:
        user = await add_user(db, **kwargs)
        await db.commit()
    return user


async def _signed_in(db, user) -> User:
    """The account as a request with its session sees it: loaded by the same
    code, in the request's own database session."""
    return await get_current_user(create_access_token({"sub": str(user.id)}), db)


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value


# ── adding a method ─────────────────────────────────────────────────────

@pytest.mark.parametrize("password", ["", "not it"])
async def test_an_authenticator_is_not_added_without_the_password(sf, password):
    async with sf() as db:
        user = await add_user(db)
        setup = await account.begin_totp(user, db)
        right_code = code_at(setup["secret"], NOW)

        refused = await _refused(account.confirm_totp(
            ConfirmTotpRequest(code=right_code, password=password), user, db))

        assert (refused.status_code, refused.detail) == (400, "Wrong password.")
        assert await status.is_on(db, user.id) is False
        # The code was not spent on the refusal: with the password it still works.
        confirmed = await account.confirm_totp(
            ConfirmTotpRequest(code=right_code, password=PASSWORD), user, db)
        assert len(confirmed["recovery_codes"]) == 10


@pytest.mark.parametrize("password", ["", "not it"])
async def test_a_passkey_is_not_added_without_the_password(sf, password):
    async with sf() as db:
        user = await add_user(db)
        device = SoftwareAuthenticator()
        offered = await account.passkey_registration_options(user, db)
        credential = device.create(offered["options"])

        refused = await _refused(account.add_passkey(
            AddPasskeyRequest(ticket=offered["ticket"], credential=credential, password=password),
            user, db))

        assert (refused.status_code, refused.detail) == (400, "Wrong password.")
        assert await passkeys.count(db, user.id) == 0
        # A mistyped password does not waste what the browser just created.
        added = await account.add_passkey(
            AddPasskeyRequest(ticket=offered["ticket"], credential=credential, password=PASSWORD),
            user, db)
        assert added["passkey"]["id"]


# ── changing the email ──────────────────────────────────────────────────

async def test_changing_the_email_needs_the_current_password(sf):
    account_row = await _account(sf)
    async with sf() as db:
        user = await _signed_in(db, account_row)
        before = user.email

        missing = await _refused(auth.update_profile(
            UpdateProfileRequest(email="thief@example.com"), db, user))
        assert (missing.status_code, missing.detail) == (
            400, "Enter your current password to change your email.")
        wrong = await _refused(auth.update_profile(
            UpdateProfileRequest(email="thief@example.com", current_password="not it"), db, user))
        assert (wrong.status_code, wrong.detail) == (400, "Wrong password.")
        assert user.email == before

        changed = await auth.update_profile(
            UpdateProfileRequest(email="New@Example.com", current_password=PASSWORD), db, user)
        assert changed["email"] == "new@example.com"

    async with sf() as db:
        assert (await db.get(User, user.id)).email == "new@example.com"


async def test_changing_only_the_name_needs_no_password(sf):
    account_row = await _account(sf)
    async with sf() as db:
        user = await _signed_in(db, account_row)

        renamed = await auth.update_profile(UpdateProfileRequest(name="Sara A."), db, user)
        same_email = await auth.update_profile(
            UpdateProfileRequest(name="Sara B.", email=user.email.upper()), db, user)

        assert renamed["name"] == "Sara A." and same_email["name"] == "Sara B."


# ── what a stolen session can do ────────────────────────────────────────

async def test_a_stolen_session_cannot_replace_the_second_step(sf):
    """Someone holds the account's session and nothing else. Before, they could
    add their own passkey, move the email to a mailbox of theirs, reset the
    password through it, and then remove the owner's authenticator."""
    async with sf() as db:
        created = await add_user(db)
        await turn_on_totp(db, created)
        email = created.email
        await db.commit()
    async with sf() as db:
        owner = await _signed_in(db, created)
        device = SoftwareAuthenticator()
        offered = await account.passkey_registration_options(owner, db)
        await db.commit()

        for call in (
            account.add_passkey(AddPasskeyRequest(
                ticket=offered["ticket"], credential=device.create(offered["options"]),
                password="a guess"), owner, db),
            auth.update_profile(UpdateProfileRequest(email="thief@example.com"), db, owner),
            account.remove_totp(PasswordRequest(password="a guess"), owner, db),
            account.regenerate_recovery_codes(PasswordRequest(password="a guess"), owner, db),
        ):
            assert (await _refused(call)).status_code == 400

    async with sf() as db:
        assert await status.methods(db, owner.id) == ["totp"]
        assert await passkeys.count(db, owner.id) == 0
        assert (await db.get(User, owner.id)).email == email
