"""The link in a password-reset email sets a new password and does nothing
else: it is not a way to be signed in."""
import os
import sys
import time
import uuid
from datetime import timedelta
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

import bcrypt
import pytest
import pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import auth
from app.core.deps import get_current_user
from app.core.security import (
    create_access_token,
    create_reset_token,
    decode_token,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import ForgotPasswordRequest, RefreshRequest, ResetPasswordRequest
from tests._rbac import build_inmemory_factory

OLD, NEW = "the old password", "a new password"


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def mailbox(monkeypatch):
    """The reset links the server would have emailed."""
    links: list[str] = []

    async def send(db, to_email, reset_link):
        links.append(reset_link)
        return True

    monkeypatch.setattr(auth, "send_password_reset_email", send)
    return links


async def _user(db) -> User:
    user = User(
        id=uuid.uuid4(), email=f"{uuid.uuid4().hex[:12]}@example.com", name="Sara",
        hashed_password=bcrypt.hashpw(OLD.encode(), bcrypt.gensalt(rounds=4)).decode(),
        is_active=True,
    )
    db.add(user)
    await db.commit()
    return user


async def _emailed_token(db, user, mailbox) -> str:
    await auth.forgot_password(ForgotPasswordRequest(email=user.email), db)
    (link,) = mailbox
    return parse_qs(urlparse(link).query)["token"][0]


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value


async def test_the_emailed_token_is_a_reset_token_for_one_hour(sf, mailbox):
    async with sf() as db:
        user = await _user(db)
        payload = decode_token(await _emailed_token(db, user, mailbox))

        assert payload["type"] == "reset" and payload["sub"] == str(user.id)
        assert 3590 <= payload["exp"] - time.time() <= 3610


async def test_the_emailed_token_does_not_sign_anyone_in(sf, mailbox):
    """Whoever can read the mailbox must not get a session out of the link:
    that would walk past the password, and past two-step verification."""
    async with sf() as db:
        user = await _user(db)
        token = await _emailed_token(db, user, mailbox)

        refused = await _refused(get_current_user(token, db))
        assert refused.status_code == 401
        # Nor can it be exchanged for a session.
        renewal = await _refused(auth.refresh_token(RefreshRequest(refresh_token=token), db))
        assert renewal.status_code == 401


async def test_the_emailed_token_sets_a_new_password(sf, mailbox):
    async with sf() as db:
        user = await _user(db)
        token = await _emailed_token(db, user, mailbox)

        answer = await auth.reset_password(ResetPasswordRequest(token=token, new_password=NEW), db)

        assert answer == {"message": "Password has been reset successfully"}
        await db.refresh(user)
        assert verify_password(NEW, user.hashed_password)
        assert not verify_password(OLD, user.hashed_password)


async def test_a_session_token_cannot_reset_a_password(sf):
    async with sf() as db:
        user = await _user(db)
        session_token = create_access_token({"sub": str(user.id)})

        refused = await _refused(auth.reset_password(
            ResetPasswordRequest(token=session_token, new_password=NEW), db))

        assert (refused.status_code, refused.detail) == (400, "Invalid reset token")
        await db.refresh(user)
        assert verify_password(OLD, user.hashed_password)


@pytest.mark.parametrize("token", ["", "nonsense", "a.b.c"])
async def test_a_made_up_token_is_refused(sf, token):
    async with sf() as db:
        refused = await _refused(auth.reset_password(
            ResetPasswordRequest(token=token, new_password=NEW), db))
        assert refused.status_code == 400


async def test_an_expired_reset_token_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        expired = create_reset_token(str(user.id), timedelta(seconds=-1))

        refused = await _refused(auth.reset_password(
            ResetPasswordRequest(token=expired, new_password=NEW), db))
        assert (refused.status_code, refused.detail) == (400, "Invalid or expired reset token")


async def test_a_reset_token_for_nobody_or_a_deactivated_account_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        user.is_active = False
        await db.commit()

        for subject in (str(uuid.uuid4()), str(user.id)):
            refused = await _refused(auth.reset_password(ResetPasswordRequest(
                token=create_reset_token(subject, timedelta(hours=1)), new_password=NEW), db))
            assert refused.status_code == 400


async def test_an_unknown_email_sends_nothing_and_says_the_same(sf, mailbox):
    async with sf() as db:
        user = await _user(db)
        known = await auth.forgot_password(ForgotPasswordRequest(email=user.email), db)
        unknown = await auth.forgot_password(ForgotPasswordRequest(email="nobody@example.com"), db)

        assert known == unknown and len(mailbox) == 1
