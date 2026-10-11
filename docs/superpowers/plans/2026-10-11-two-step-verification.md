# Two-Step Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the password, an account can be asked for a second proof — a code from an authenticator app or a passkey — with recovery codes for a lost device, a switch for administrators to require it, and no way around it through the registry, a backup, or a password-reset email.

**Architecture:** A new backend package, `app.services.two_step`, in small modules: authenticator codes, recovery codes, passkeys, tickets, wrong tries, what an account has, and one function that decides what a correct password gets (a session, a ticket for the second step, or a ticket for the forced setup). Login, sign-up and invitations call that one function instead of building tokens themselves. Two thin routers under `/auth/two-step` expose it: one public and ticket-gated for signing in, one for the signed-in user's own account. Four new tables hold the state; the requirement is one row in the existing system settings. The frontend keeps a pending sign-in in memory and shows its next step in the sign-in card, and gains two settings tabs.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 async, `pyotp` (codes), `webauthn` (passkeys), `bcrypt`, Alembic, pytest with `asyncio_mode=auto` on in-memory SQLite with foreign keys on (`tests/_rbac.py`); Next.js 16 / React 19 / TypeScript, `@simplewebauthn/browser`, `qrcode`.

**Spec:** `docs/superpowers/specs/2026-10-11-two-step-verification-design.md` (read its "Amendments found while planning" section first: this plan implements the spec as amended there)

## Global Constraints

- **Branch:** all work happens on `feat/two-step-verification`. Never commit to `main`.
- **Commits:** conventional style (`feat(two-step): …`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **The name** is "Two-step verification" in every string a user sees. Never "2FA" or "MFA" in the interface.
- **The password always comes first.** A passkey is never a way to skip it.
- **A ticket is never a session.** It is 32 random bytes; only its SHA-256 is stored; it has one purpose (`verify`, `setup`, `register`) and a lifetime (5, 15 and 5 minutes). Only a correct second step, or a finished setup, returns tokens.
- **A setup ticket adds one method** and then becomes `setup_done`, which opens nothing but `/setup/finish`. It is refused once the account has a method that was not added with it.
- **Codes:** 6 digits, 30 seconds, SHA-1, 160-bit secret; the current step and one either side are accepted; a step, and every earlier one, is accepted once. The secret is stored encrypted with `MEGOOCI_SECRET_KEY` and counts only once a correct code has confirmed it.
- **Recovery codes:** ten, shown as `xxxxx-xxxxx`, from the alphabet `abcdefghjkmnpqrstuvwxyz23456789`, hashed with bcrypt at cost 10, each used once. They are generated when an account's first method is added and deleted when its last method is removed.
- **Wrong tries:** five in a row pause the second step for that account for five minutes. The count lives in the database, never in Redis.
- **Passkeys:** the relying party is the host of `MEGOOCI_PUBLIC_URL`, the only accepted origin is that URL's origin, user verification is "preferred", no attestation, ten per account at most. A challenge is good for one answer.
- **Messages, exactly:** `That code is not right.` · `That passkey was not accepted.` · `That took too long. Sign in again.` · `Too many wrong tries. Try again in N minutes.` · `Wrong password.` · `Two-step verification is required on this server.` · `Set up two-step verification on your own account first.` · `This account uses two-step verification. Use an API token as the password (Settings → API tokens).`
- **Status codes on the sign-in routes:** a wrong answer is 401, a ticket that is unknown, expired or used is 410, a pause is 429.
- **An API token cannot manage two-step verification.** The own-account routes need a browser session.
- **Nothing secret is logged or returned:** no secret, code, recovery code, ticket or hash in a log line, an audit entry or a summary.
- **Sessions are unchanged:** 12-hour access tokens, 30-day refresh. The only new rule is that a session does not renew while two-step verification is required and the account has none.
- **The time** used by the two-step code comes from `app.services.two_step.clock.now()`. Tests set it with `monkeypatch.setattr("app.services.two_step.clock.now", …)`.
- **Backend tests:** run from `backend/` with `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`. The suite has 924 tests before this plan and 1249 after it.
- **Test databases:** in-memory SQLite, which returns naive datetimes; compare stored times through `state.aware(...)`, and give `synchronize_session=False` to bulk deletes and updates that compare a time.
- **Frontend gate:** `npx tsc --noEmit` from `frontend/`. There is no frontend unit-test harness, and `npm run lint` is broken; do not use it.
- **Line endings:** existing files use CRLF in the working tree. Keep them: use the Edit tool, not a script that rewrites a file with LF.
- **Edits are exact.** Where a step says "Replace … with …", the first block occurs exactly once in the file at that point of the plan; replace that text and nothing else.
- **Paths in commands:** `git` commands use paths relative to the repository root. Commands are written for Git Bash.

## Review Focus

Inputs and situations the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **A password-reset email's link used as a session.** Today the link carries a token that signs its holder in for an hour, which would walk past the second step. Expected: it sets a new password and nothing else. Pinned in Task 1 (`test_the_emailed_token_does_not_sign_anyone_in`, `test_the_emailed_token_sets_a_new_password`).
2. **Someone who has only the password holds a setup ticket while the owner sets two-step verification up from their own session.** Expected: the ticket adds nothing and never becomes a session. Pinned in Task 8 (`test_a_setup_ticket_is_worthless_once_the_account_has_a_method_from_elsewhere`).
3. **The same code, recovery code or passkey answer twice, or from two browsers at once.** Expected: the second is refused. Pinned in Task 3 (`test_a_code_works_once`), Task 4 (`test_a_code_works_once`), Task 8 (`test_a_code_cannot_be_used_from_two_browsers`, `test_a_passkey_answer_cannot_be_replayed`, `test_a_challenge_is_spent_by_a_wrong_answer`).
4. **A code typed with spaces, or on an Arabic or Persian keyboard (١٢٣٤٥٦).** Expected: read as its digits, never a server error. Pinned in Task 3 (`test_digits_typed_on_an_arabic_keyboard_are_read_as_digits`, `test_anything_that_is_not_six_digits_is_wrong`).
5. **A malformed passkey answer or request body.** Expected: refused as a wrong answer or 422, never a 500. Pinned in Task 6 (`test_a_malformed_answer_is_refused_not_a_crash`, `test_a_damaged_answer_is_refused_not_a_crash`), Task 8 (`test_a_malformed_passkey_answer_is_a_wrong_answer`) and Task 10 (`test_malformed_requests_are_rejected_before_anything_is_looked_up`).
6. **An authenticator that never counts (most phones and laptops report 0), and one whose counter goes backwards.** Expected: the first keeps working; the second is refused and recorded. Pinned in Task 6 (`test_an_authenticator_that_does_not_count_keeps_working`, `test_a_counter_that_went_back_is_refused_and_marked`).
7. **An account deactivated, or reset by an administrator, between the password and the second step, or between setup and finish.** Expected: no session. Pinned in Task 8 (`test_an_account_deactivated_in_between_cannot_finish`, `test_a_reset_between_setup_and_finish_leaves_no_session`).
8. **Restoring a backup that knows the restoring administrator under another id, or that gives a passkey they hold now to someone else.** Expected: their two-step verification is exactly what it was before the restore. Pinned in Task 12 (`test_two_step_follows_an_administrator_the_backup_knows_under_another_id`, `test_a_passkey_the_administrator_holds_now_is_not_given_to_someone_else`).
9. **`docker login` with a wrong password for an account that uses two-step verification, or with an API token whose scope does not cover the registry.** Expected: the first learns nothing about the account; the second is told why. Pinned in Task 11 (`test_a_wrong_password_learns_nothing_about_the_account`, `test_a_token_scoped_to_something_else_is_refused_with_the_reason`).
10. **An administrator resetting their own two-step verification from the Users page.** Expected: refused, because in Settings the same change asks for the password. Pinned in Task 10 (`test_an_administrator_cannot_reset_themselves_this_way`).
11. **A ticket handed back while an earlier one is still loaded (naive against aware times on SQLite).** Expected: no error. Pinned in Task 5 (`test_issuing_works_while_an_earlier_ticket_is_in_hand`).

**Not exercised by any test, and to be tried by hand before relying on it** (Task 16 lists them): a real authenticator app, a real fingerprint reader or security key, every screen in a browser, the app opened at an address other than `MEGOOCI_PUBLIC_URL`, and a real `docker login`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `backend/app/core/security.py` | Modify | `create_reset_token`: a reset link's token is not an access token. |
| `backend/app/models/two_step.py` | Create | The four tables. |
| `backend/alembic/versions/024_two_step_verification.py` | Create | The migration. |
| `backend/app/services/two_step/__init__.py` | Create | Package description. |
| `backend/app/services/two_step/state.py` | Create | The per-user row; naive-to-aware times. |
| `backend/app/services/two_step/totp.py` | Create | Authenticator codes: set up, confirm, check, remove. |
| `backend/app/services/two_step/recovery_codes.py` | Create | Generate, use, count, clear. |
| `backend/app/services/two_step/tickets.py` | Create | Issue, find, challenge, consume. |
| `backend/app/services/two_step/attempts.py` | Create | The wrong-try count and the pause. |
| `backend/app/services/two_step/passkeys.py` | Create | WebAuthn: register, verify, rename, remove. |
| `backend/app/services/two_step/status.py` | Create | What an account has; the requirement; the summary. |
| `backend/app/services/two_step/sign_in.py` | Create | Session, second step or setup, for a correct password. |
| `backend/app/services/two_step/reset.py` | Create | Remove everything an account has. |
| `backend/app/services/two_step/clock.py` | Create | The time, in one place. |
| `backend/app/schemas/two_step.py` | Create | Request and response models. |
| `backend/app/api/v1/two_step.py` | Create | Public, ticket-gated routes: second step and forced setup. |
| `backend/app/api/v1/two_step_account.py` | Create | The signed-in user's own account. |
| `backend/app/api/v1/auth.py`, `invites.py` | Modify | Use the sign-in decision; `me`; renewal; the reset token. |
| `backend/app/api/v1/router.py` | Modify | Mount both routers at `/auth/two-step`. |
| `backend/app/core/deps.py` | Modify | Mark requests authenticated with an API token. |
| `backend/app/api/v1/system.py` | Modify | `GET`/`PUT /system/security`. |
| `backend/app/api/v1/users.py`, `backend/app/schemas/users.py` | Modify | `two_step_enabled`; the reset route. |
| `backend/app/reset_two_step.py` | Create | The server command. |
| `backend/app/core/registry_auth.py`, `backend/app/api/v1/registry_oci.py` | Modify | API token as the password; the refusal docker prints. |
| `backend/app/core/token_scopes.py` | Modify | A "Container registry" scope. |
| `backend/app/services/backup/tables.py`, `restore.py` | Modify | Three tables in backups; the restoring administrator keeps theirs. |
| `backend/pyproject.toml` | Modify | `pyotp`, `webauthn`. |
| `backend/tests/_passkey.py`, `_two_step.py` | Create | A software passkey; accounts and methods for tests. |
| `backend/tests/test_password_reset_token.py`, `test_two_step_*.py` | Create | Thirteen test files, one or two per task. |
| `backend/tests/conftest.py`, `_backup.py`, `test_backup_tables.py`, `test_token_scopes.py` | Modify | Cheap code hashes in tests; the new tables and scope. |
| `frontend/src/lib/api.ts`, `auth.ts` | Modify | Types, `twoStepApi`, the pending sign-in. |
| `frontend/src/lib/passkeys.ts` | Create | The browser's passkey prompts, and why they may be unavailable. |
| `frontend/src/components/two-step/*.tsx` | Create | Authenticator setup, recovery codes, password prompt. |
| `frontend/src/components/login/second-step.tsx`, `two-step-setup.tsx` | Create | The second step; the forced setup. |
| `frontend/src/components/login/sign-in-form.tsx`, `frontend/src/app/login`, `signup`, `invite/accept` | Modify | Hand over to those screens. |
| `frontend/src/components/settings/two-step-tab.tsx`, `security-tab.tsx` | Create | The two settings tabs. |
| `frontend/src/app/settings/page.tsx` | Modify | Add the tabs. |
| `frontend/src/app/admin/users/page.tsx`, `frontend/src/components/layout/app-layout.tsx` | Modify | The badge and reset; the notice. |
| `frontend/package.json`, `package-lock.json` | Modify | `@simplewebauthn/browser`, `qrcode`, `@types/qrcode`. |
| `README.md` | Modify | "Two-Step Verification" section. |

---

### Task 1: A password-reset link is not a session

This is a defect that exists today and would undo everything that follows. `forgot_password` builds the link's token with `create_access_token`, which stamps every token as type `access` over whatever type it was given. So the emailed link signs its holder in for an hour, and `reset_password`, which asks for type `reset`, has never accepted it.

**Files:**
- Modify: `backend/app/core/security.py`, `backend/app/api/v1/auth.py`
- Test: `backend/tests/test_password_reset_token.py` (new)

**Interfaces:**
- Produces: `create_reset_token(user_id: str, expires_delta: timedelta) -> str` in `app.core.security`: a JWT with `sub`, `type: "reset"` and `exp`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_password_reset_token.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_password_reset_token.py
```

Expected: a collection error, `ImportError: cannot import name 'create_reset_token' from 'app.core.security'`.

- [ ] **Step 3: Make the reset token its own kind**

**`backend/app/core/security.py`**. Replace:

````python
def create_refresh_token(data: dict) -> str:
````

with:

````python
def create_reset_token(user_id: str, expires_delta: timedelta) -> str:
    """A token for the link in a password-reset email.

    It is not an access token: its type is ``reset``, so nothing that asks
    for a signed-in user accepts it. Whoever reads the email can set a new
    password with it, and nothing more.
    """
    settings = get_settings()
    payload = {
        "sub": user_id,
        "type": "reset",
        "exp": datetime.now(timezone.utc) + expires_delta,
    }
    return jwt.encode(
        payload,
        settings.MEGOOCI_JWT_SECRET,
        algorithm=settings.MEGOOCI_JWT_ALGORITHM,
    )


def create_refresh_token(data: dict) -> str:
````

**`backend/app/api/v1/auth.py`** — edit 1 of 2. Replace:

````python
    create_refresh_token,
    decode_token,
````

with:

````python
    create_refresh_token,
    create_reset_token,
    decode_token,
````

**`backend/app/api/v1/auth.py`** — edit 2 of 2. Replace:

````python
        token = create_access_token(
            {"sub": str(user.id), "type": "reset"},
            expires_delta=timedelta(hours=1),
        )
````

with:

````python
        token = create_reset_token(str(user.id), timedelta(hours=1))
````

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_password_reset_token.py
```

Expected: `10 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/security.py backend/app/api/v1/auth.py backend/tests/test_password_reset_token.py
git commit -m "fix(auth): a password-reset link is not a session

The link's token was built as an access token, so it signed its holder
in for an hour, and the reset endpoint, which wants a reset token, never
accepted it.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The tables and the migration

**Files:**
- Create: `backend/app/models/two_step.py`, `backend/alembic/versions/024_two_step_verification.py`
- Modify: `backend/app/models/__init__.py`, `backend/pyproject.toml`
- Test: `backend/tests/test_two_step_models.py` (new)

**Interfaces:**
- Produces: `UserTwoStep` (`user_id` primary key, `totp_secret_encrypted`, `totp_confirmed_at`, `totp_last_step`, `totp_last_used_at`, `recovery_codes_generated_at`, `failed_attempts`, `locked_until`), `UserPasskey` (`id`, `user_id`, `credential_id` unique, `public_key`, `sign_count`, `transports`, `name`, `created_at`, `last_used_at`), `UserRecoveryCode` (`id`, `user_id`, `code_hash`, `used_at`, `created_at`), `TwoStepTicket` (`id`, `user_id`, `purpose`, `token_hash` unique, `webauthn_challenge`, `expires_at`, `created_at`), all in `app.models.two_step`. Every `user_id` deletes with the user.

- [ ] **Step 1: Install the two libraries**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pip install pyotp webauthn
```

Expected: both install, with `cbor2` and `pyOpenSSL` as dependencies of `webauthn`.

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_two_step_models.py`:

```python
"""The four tables of two-step verification, and the migration that makes them."""
import importlib.util
import os
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.models.base import Base
from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.models.user import User
from tests._rbac import build_inmemory_factory, seed_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


def _passkey(user_id, credential_id=b"cred-1", name="Laptop") -> UserPasskey:
    return UserPasskey(user_id=user_id, credential_id=credential_id, public_key=b"key", name=name)


async def test_a_new_row_starts_with_nothing_set_up(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        db.add(UserTwoStep(user_id=user_id))
        await db.commit()

    async with sf() as db:
        row = await db.get(UserTwoStep, user_id)
        assert row.totp_secret_encrypted is None and row.totp_confirmed_at is None
        assert row.failed_attempts == 0 and row.locked_until is None


async def test_a_passkey_keeps_its_bytes_and_starts_unused(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        db.add(UserPasskey(
            user_id=user_id, credential_id=b"\x00\x01\xff", public_key=b"\xa5\x01\x02",
            transports=["usb", "nfc"], name="YubiKey",
        ))
        await db.commit()

    async with sf() as db:
        passkey = (await db.execute(sa.select(UserPasskey))).scalar_one()
        assert passkey.credential_id == b"\x00\x01\xff" and passkey.public_key == b"\xa5\x01\x02"
        assert passkey.transports == ["usb", "nfc"]
        assert passkey.sign_count == 0 and passkey.last_used_at is None


async def test_one_credential_belongs_to_one_passkey(sf):
    async with sf() as db:
        first, second = await seed_user(db), await seed_user(db)
        db.add(_passkey(first))
        await db.commit()
        db.add(_passkey(second))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_one_token_belongs_to_one_ticket(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        for _ in range(2):
            db.add(TwoStepTicket(
                user_id=user_id, purpose="verify", token_hash="a" * 64,
                expires_at=NOW + timedelta(minutes=5),
            ))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_everything_goes_when_the_user_is_deleted(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        other = await seed_user(db)
        db.add_all([
            UserTwoStep(user_id=user_id),
            _passkey(user_id),
            UserRecoveryCode(user_id=user_id, code_hash="h"),
            TwoStepTicket(user_id=user_id, purpose="setup", token_hash="b" * 64,
                          expires_at=NOW + timedelta(minutes=15)),
            _passkey(other, credential_id=b"cred-2"),
        ])
        await db.commit()
        await db.execute(sa.delete(User).where(User.id == user_id))
        await db.commit()

    async with sf() as db:
        for model in (UserTwoStep, UserRecoveryCode, TwoStepTicket):
            assert (await db.execute(sa.select(model))).scalars().all() == []
        assert [p.user_id for p in (await db.execute(sa.select(UserPasskey))).scalars()] == [other]


# ── the migration ───────────────────────────────────────────────────────

_TABLES = ("user_two_step", "user_passkeys", "user_recovery_codes", "two_step_tickets")


@pytest.fixture
def migration(monkeypatch):
    """The migration module, with Alembic's ``op`` replaced by a recorder.

    The real thing is run against PostgreSQL by hand; here it only has to be
    importable without Alembic installed, and say what it would create.
    """
    recorder = types.SimpleNamespace(created={}, dropped=[])

    def create_table(name, *columns, **_):
        recorder.created[name] = {
            column.name for column in columns if isinstance(column, sa.Column)
        }

    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(create_table=create_table, drop_table=recorder.dropped.append)
    monkeypatch.setitem(sys.modules, "alembic", fake)

    path = pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "024_two_step_verification.py"
    spec = importlib.util.spec_from_file_location("migration_024", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.recorder = recorder
    return module


def test_the_migration_follows_023(migration):
    assert (migration.revision, migration.down_revision) == ("024", "023")


def test_the_migration_creates_the_columns_the_models_have(migration):
    migration.upgrade()

    assert set(migration.recorder.created) == set(_TABLES)
    for name in _TABLES:
        assert migration.recorder.created[name] == set(Base.metadata.tables[name].columns.keys()), name


def test_the_downgrade_drops_the_four_tables(migration):
    migration.downgrade()
    assert sorted(migration.recorder.dropped) == sorted(_TABLES)
```

- [ ] **Step 3: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_models.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.models.two_step'`.

- [ ] **Step 4: Write the models and the migration**

Create `backend/app/models/two_step.py`:

```python
"""Two-step verification: what an account proves itself with after its password.

An account has two-step verification on when it has a confirmed authenticator
app (``UserTwoStep.totp_confirmed_at``) or at least one passkey.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class UserTwoStep(Base):
    """One row per user who has started with two-step verification."""

    __tablename__ = "user_two_step"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # The authenticator secret, encrypted with MEGOOCI_SECRET_KEY. It counts
    # only once a correct code has confirmed it.
    totp_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The last 30-second step a code was accepted for: a code works once.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    totp_last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recovery_codes_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Wrong tries at the second step, and until when it is paused.
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserPasskey(Base):
    __tablename__ = "user_passkeys"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True, nullable=False)
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    transports: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UserRecoveryCode(Base):
    __tablename__ = "user_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # Hashed like a password: a copy of the database does not give the codes away.
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TwoStepTicket(Base):
    """Proof that a browser gave the right password, good for one purpose and
    a few minutes. Never a session."""

    __tablename__ = "two_step_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    # "verify" (the second step), "setup" (forced setup) or "register" (adding
    # a passkey from Settings).
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    # SHA-256 of the random token the browser holds.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    webauthn_challenge: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
```

Create `backend/alembic/versions/024_two_step_verification.py`:

```python
"""Two-step verification: authenticator app, passkeys, recovery codes, tickets

Revision ID: 024
Revises: 023
Create Date: 2026-10-11
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def _user_id(**kwargs) -> sa.Column:
    return sa.Column(
        "user_id", UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="CASCADE"),
        **kwargs,
    )


def upgrade() -> None:
    op.create_table(
        "user_two_step",
        _user_id(primary_key=True),
        sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
        sa.Column("totp_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("totp_last_step", sa.BigInteger(), nullable=True),
        sa.Column("totp_last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_codes_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_passkeys",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, index=True),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False, unique=True),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("transports", JSON(), nullable=False, server_default="[]"),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "user_recovery_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, index=True),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "two_step_tickets",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        _user_id(nullable=False, index=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("webauthn_challenge", sa.LargeBinary(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("two_step_tickets")
    op.drop_table("user_recovery_codes")
    op.drop_table("user_passkeys")
    op.drop_table("user_two_step")
```

Register the models and declare the dependencies:

**`backend/app/models/__init__.py`** — edit 1 of 2. Replace:

````python
from app.models.trigger import Trigger, WebhookEndpoint
````

with:

````python
from app.models.trigger import Trigger, WebhookEndpoint
from app.models.two_step import (
    TwoStepTicket,
    UserPasskey,
    UserRecoveryCode,
    UserTwoStep,
)
````

**`backend/app/models/__init__.py`** — edit 2 of 2. Replace:

````python
    "SystemSetting",
]
````

with:

````python
    "SystemSetting",
    "UserTwoStep",
    "UserPasskey",
    "UserRecoveryCode",
    "TwoStepTicket",
]
````

**`backend/pyproject.toml`**. Replace:

````
    "boto3",
````

with:

````
    "boto3",
    "pyotp",
    "webauthn",
````

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_models.py
```

Expected: `8 passed`.

- [ ] **Step 6: Apply and revert the migration on a real PostgreSQL**

The tests above only check what the migration would create. Run it for real in a throwaway container. Alembic is a declared dependency that the test venv may lack: if `./.venv/Scripts/python.exe -m alembic --version` fails, run `./.venv/Scripts/python.exe -m pip install alembic` first. Then, from `backend/`:

```bash
(
  set -e
  trap 'docker rm -f megooci-migration-check >/dev/null 2>&1' EXIT
  # A port Docker picks, on this machine only: nothing here may reach a database that is already running.
  docker run -d --rm --name megooci-migration-check -e POSTGRES_PASSWORD=check -e POSTGRES_DB=megooci -p 127.0.0.1::5432 postgres:16-alpine >/dev/null
  PORT=$(docker port megooci-migration-check 5432/tcp | sed 's/.*://')
  until docker exec megooci-migration-check pg_isready -U postgres -d megooci >/dev/null 2>&1; do sleep 1; done; sleep 2
  export MEGOOCI_DATABASE_URL="postgresql+asyncpg://postgres:check@127.0.0.1:$PORT/megooci"
  count() { docker exec megooci-migration-check psql -U postgres -d megooci -Atc "select count(*) from pg_tables where tablename in ('user_two_step','user_passkeys','user_recovery_codes','two_step_tickets')"; }
  ./.venv/Scripts/python.exe -m alembic upgrade head 2>&1 | tail -1; echo "tables: $(count)"
  ./.venv/Scripts/python.exe -m alembic downgrade 023 2>&1 | tail -1; echo "tables: $(count)"
  ./.venv/Scripts/python.exe -m alembic upgrade head 2>&1 | tail -1; echo "tables: $(count)"
)
```

Expected: `tables: 4`, then `tables: 0`, then `tables: 4`; the first upgrade's last line is `Running upgrade 023 -> 024`. The container is removed when the block ends, whether or not it worked.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/two_step.py backend/app/models/__init__.py backend/alembic/versions/024_two_step_verification.py backend/pyproject.toml backend/tests/test_two_step_models.py
git commit -m "feat(two-step): tables for authenticator, passkeys, recovery codes and tickets

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Authenticator codes

**Files:**
- Create: `backend/app/services/two_step/__init__.py`, `backend/app/services/two_step/state.py`, `backend/app/services/two_step/totp.py`
- Test: `backend/tests/test_two_step_totp.py` (new)

**Interfaces:**
- Consumes: `UserTwoStep` (Task 2); `encrypt_secret(plaintext: str, key: str) -> bytes` and `decrypt_secret(ciphertext: bytes, key: str) -> str` from `app.core.security`.
- Produces, in `state`: `aware(value: datetime | None) -> datetime | None`, `get_state(db, user_id) -> UserTwoStep | None`, `ensure_state(db, user_id) -> UserTwoStep`.
- Produces, in `totp`: `new_secret() -> str`, `otpauth_uri(secret: str, email: str) -> str`, `matching_step(secret: str, code: str, at: datetime) -> int | None`, `is_confirmed(state: UserTwoStep | None) -> bool`, `AlreadySetUp(Exception)`, `begin(db, user: User) -> tuple[str, str]` (secret, address), `confirm(db, user_id, code: str, now: datetime) -> bool`, `check(db, user_id, code: str, now: datetime) -> bool`, `remove(db, user_id) -> bool`. None of them commits.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_totp.py`:

```python
"""Authenticator codes: which are accepted, and that each works once."""
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import pyotp
import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.models.two_step import UserTwoStep
from app.models.user import User
from app.services.two_step import totp
from tests._rbac import build_inmemory_factory, seed_user

# The middle of a 30-second step, so "30 seconds later" is exactly the next one.
NOW = datetime(2026, 10, 11, 12, 0, 15, tzinfo=timezone.utc)
STEP = timedelta(seconds=30)
SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def code_at(secret: str, when: datetime) -> str:
    return pyotp.TOTP(secret).at(when)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


# ── which code is right ─────────────────────────────────────────────────

def test_the_current_code_is_right():
    assert totp.matching_step(SECRET, code_at(SECRET, NOW), NOW) == int(NOW.timestamp()) // 30


@pytest.mark.parametrize("offset", [-1, 1])
def test_a_clock_one_step_off_is_allowed(offset):
    there = NOW + offset * STEP
    assert totp.matching_step(SECRET, code_at(SECRET, there), NOW) == int(there.timestamp()) // 30


@pytest.mark.parametrize("offset", [-2, 2, -10, 10])
def test_a_code_from_further_away_is_wrong(offset):
    assert totp.matching_step(SECRET, code_at(SECRET, NOW + offset * STEP), NOW) is None


def test_a_code_for_another_secret_is_wrong():
    other = totp.new_secret()
    assert totp.matching_step(SECRET, code_at(other, NOW), NOW) is None


def test_spaces_in_a_code_do_not_matter():
    code = code_at(SECRET, NOW)
    assert totp.matching_step(SECRET, f" {code[:3]} {code[3:]}\n", NOW) is not None


def test_digits_typed_on_an_arabic_keyboard_are_read_as_digits():
    code = code_at(SECRET, NOW)
    arabic = code.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))
    persian = code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

    assert arabic != code
    assert totp.matching_step(SECRET, arabic, NOW) is not None
    assert totp.matching_step(SECRET, persian, NOW) is not None


@pytest.mark.parametrize("text", ["", "12345", "1234567", "abcdef", "12 34", "12345a", "-12345", "²³¹²³¹", "12345½"])
def test_anything_that_is_not_six_digits_is_wrong(text):
    assert totp.matching_step(SECRET, text, NOW) is None


def test_a_new_secret_is_160_bits_of_base32():
    secret = totp.new_secret()
    assert len(secret) == 32 and secret != totp.new_secret()
    assert set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")


def test_the_address_names_the_issuer_and_the_account():
    uri = urllib.parse.urlparse(totp.otpauth_uri(SECRET, "sara+ci@example.com"))
    query = urllib.parse.parse_qs(uri.query)
    assert (uri.scheme, uri.netloc) == ("otpauth", "totp")
    assert urllib.parse.unquote(uri.path) == "/MegooCI:sara+ci@example.com"
    assert query["secret"] == [SECRET] and query["issuer"] == ["MegooCI"]


# ── setting it up ───────────────────────────────────────────────────────

async def _user(db) -> User:
    return await db.get(User, await seed_user(db))


async def test_nothing_is_on_until_a_code_confirms_it(sf):
    async with sf() as db:
        user = await _user(db)
        secret, uri = await totp.begin(db, user)
        state = await db.get(UserTwoStep, user.id)

        assert secret in uri
        assert not totp.is_confirmed(state)
        # A code for a secret that is not confirmed does not sign anyone in.
        assert await totp.check(db, user.id, code_at(secret, NOW), NOW) is False


async def test_the_secret_is_stored_encrypted(sf):
    async with sf() as db:
        user = await _user(db)
        secret, _ = await totp.begin(db, user)
        stored = (await db.get(UserTwoStep, user.id)).totp_secret_encrypted

        assert secret.encode() not in bytes(stored)
        assert stored.startswith(b"gAAAA")  # a Fernet token


async def test_a_right_code_confirms_and_a_wrong_one_does_not(sf):
    async with sf() as db:
        user = await _user(db)
        secret, _ = await totp.begin(db, user)

        assert await totp.confirm(db, user.id, "000000", NOW) is False
        assert not totp.is_confirmed(await db.get(UserTwoStep, user.id))

        assert await totp.confirm(db, user.id, code_at(secret, NOW), NOW) is True
        state = await db.get(UserTwoStep, user.id)
        assert totp.is_confirmed(state) and state.totp_last_used_at is not None


async def test_the_confirming_code_cannot_also_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        secret, _ = await totp.begin(db, user)
        code = code_at(secret, NOW)
        await totp.confirm(db, user.id, code, NOW)

        assert await totp.check(db, user.id, code, NOW) is False
        assert await totp.check(db, user.id, code_at(secret, NOW + STEP), NOW + STEP) is True


async def test_starting_again_replaces_an_unconfirmed_secret(sf):
    async with sf() as db:
        user = await _user(db)
        first, _ = await totp.begin(db, user)
        second, _ = await totp.begin(db, user)

        assert first != second
        assert await totp.confirm(db, user.id, code_at(first, NOW), NOW) is False
        assert await totp.confirm(db, user.id, code_at(second, NOW), NOW) is True


async def test_a_confirmed_authenticator_cannot_be_set_up_over(sf):
    async with sf() as db:
        user = await _user(db)
        secret, _ = await totp.begin(db, user)
        await totp.confirm(db, user.id, code_at(secret, NOW), NOW)

        with pytest.raises(totp.AlreadySetUp):
            await totp.begin(db, user)
        # ...and confirming again changes nothing.
        assert await totp.confirm(db, user.id, code_at(secret, NOW + STEP), NOW + STEP) is False


async def test_confirming_without_starting_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        assert await totp.confirm(db, user.id, "123456", NOW) is False


# ── signing in with it ──────────────────────────────────────────────────

async def _confirmed(db) -> tuple[User, str]:
    user = await _user(db)
    secret, _ = await totp.begin(db, user)
    await totp.confirm(db, user.id, code_at(secret, NOW - 10 * STEP), NOW - 10 * STEP)
    return user, secret


async def test_a_code_works_once(sf):
    async with sf() as db:
        user, secret = await _confirmed(db)
        code = code_at(secret, NOW)

        assert await totp.check(db, user.id, code, NOW) is True
        assert await totp.check(db, user.id, code, NOW) is False
        assert await totp.check(db, user.id, code, NOW + STEP) is False


async def test_an_earlier_code_is_refused_after_a_later_one(sf):
    async with sf() as db:
        user, secret = await _confirmed(db)
        earlier, later = code_at(secret, NOW - STEP), code_at(secret, NOW)

        assert await totp.check(db, user.id, later, NOW) is True
        assert await totp.check(db, user.id, earlier, NOW) is False


async def test_the_next_code_works_after_the_last(sf):
    async with sf() as db:
        user, secret = await _confirmed(db)

        assert await totp.check(db, user.id, code_at(secret, NOW), NOW) is True
        assert await totp.check(db, user.id, code_at(secret, NOW + STEP), NOW + STEP) is True


async def test_a_wrong_code_changes_nothing(sf):
    async with sf() as db:
        user, secret = await _confirmed(db)
        before = (await db.get(UserTwoStep, user.id)).totp_last_step

        assert await totp.check(db, user.id, "000000", NOW) is False
        assert (await db.get(UserTwoStep, user.id)).totp_last_step == before
        assert await totp.check(db, user.id, code_at(secret, NOW), NOW) is True


async def test_an_account_without_an_authenticator_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        assert await totp.check(db, user.id, "123456", NOW) is False


async def test_one_accounts_code_is_not_anothers(sf):
    async with sf() as db:
        first, first_secret = await _confirmed(db)
        second, _ = await _confirmed(db)

        assert await totp.check(db, second.id, code_at(first_secret, NOW), NOW) is False
        assert await totp.check(db, first.id, code_at(first_secret, NOW), NOW) is True


# ── removing it ─────────────────────────────────────────────────────────

async def test_removing_turns_it_off_and_forgets_the_secret(sf):
    async with sf() as db:
        user, secret = await _confirmed(db)

        assert await totp.remove(db, user.id) is True
        state = await db.get(UserTwoStep, user.id)
        assert state.totp_secret_encrypted is None and not totp.is_confirmed(state)
        assert await totp.check(db, user.id, code_at(secret, NOW), NOW) is False
        assert await totp.remove(db, user.id) is False


async def test_it_can_be_set_up_again_after_removal(sf):
    async with sf() as db:
        user, old = await _confirmed(db)
        await totp.remove(db, user.id)
        new, _ = await totp.begin(db, user)

        assert new != old
        assert await totp.confirm(db, user.id, code_at(new, NOW), NOW) is True
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_totp.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.two_step'`.

- [ ] **Step 3: Write the package, the per-user row and the codes**

Create `backend/app/services/two_step/__init__.py`:

```python
"""Two-step verification: a second proof after the password.

- ``state``           the per-user row
- ``totp``            authenticator-app codes
- ``recovery_codes``  one-time codes for a lost device
- ``passkeys``        WebAuthn credentials
- ``tickets``         short-lived proof that the password was right
- ``attempts``        wrong tries, and the pause after too many
- ``status``          what an account has; whether the server requires it
- ``sign_in``         session, second step or setup, for a proven password
- ``reset``           remove everything an account has
- ``clock``           the time, in one place so tests can set it
"""
```

Create `backend/app/services/two_step/state.py`:

```python
"""The per-user row of two-step verification."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import UserTwoStep


def aware(value: datetime | None) -> datetime | None:
    """SQLite (tests) returns naive datetimes; they are stored as UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


async def get_state(db: AsyncSession, user_id: uuid.UUID) -> UserTwoStep | None:
    return await db.get(UserTwoStep, user_id)


async def ensure_state(db: AsyncSession, user_id: uuid.UUID) -> UserTwoStep:
    """The user's row, created if this is the first time it is needed."""
    state = await db.get(UserTwoStep, user_id)
    if state is None:
        state = UserTwoStep(user_id=user_id, failed_attempts=0)
        try:
            # In a savepoint, so that losing the race below undoes this insert
            # and nothing else of the request.
            async with db.begin_nested():
                db.add(state)
                await db.flush()
        except IntegrityError:
            # Another request of the same user created it in the meantime.
            state = await db.get(UserTwoStep, user_id)
    return state
```

Create `backend/app/services/two_step/totp.py`:

```python
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_totp.py
```

Expected: `36 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/two_step/__init__.py backend/app/services/two_step/state.py backend/app/services/two_step/totp.py backend/tests/test_two_step_totp.py
git commit -m "feat(two-step): authenticator-app codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Recovery codes

**Files:**
- Create: `backend/app/services/two_step/recovery_codes.py`
- Modify: `backend/tests/conftest.py`
- Test: `backend/tests/test_two_step_recovery_codes.py` (new)

**Interfaces:**
- Consumes: `UserRecoveryCode` (Task 2); `ensure_state`, `get_state` (Task 3).
- Produces: `COUNT = 10`, `HASH_ROUNDS = 10`, `new_code() -> str`, `normalise(text: str) -> str | None`, `generate(db, user_id, now: datetime) -> list[str]` (replaces the account's codes; the only time they exist in the clear), `use(db, user_id, code: str, now: datetime) -> bool`, `remaining(db, user_id) -> int`, `clear(db, user_id) -> None`. None of them commits.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_recovery_codes.py`:

```python
"""Recovery codes: ten, shown once, each good for one use."""
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.models.two_step import UserRecoveryCode, UserTwoStep
from app.services.two_step import recovery_codes
from tests._rbac import build_inmemory_factory, seed_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


# ── what a code looks like ──────────────────────────────────────────────

def test_a_code_is_two_groups_of_five_without_lookalike_characters():
    codes = {recovery_codes.new_code() for _ in range(200)}

    assert len(codes) == 200
    for code in codes:
        assert re.fullmatch(r"[a-z2-9]{5}-[a-z2-9]{5}", code), code
        assert not set(code) & set("01lio"), code


@pytest.mark.parametrize("typed", [
    "abcde-fghjk", "ABCDE-FGHJK", "abcdefghjk", " abcde fghjk ", "abcde-fghjk\n", "Abcde - Fghjk",
])
def test_a_code_may_be_typed_loosely(typed):
    assert recovery_codes.normalise(typed) == "abcdefghjk"


@pytest.mark.parametrize("typed", [
    "", "abcde", "abcde-fghj", "abcde-fghjkm", "abcde-fghj0", "abcde-fghjl", "abcde_fghjk", "123456",
    "абвгд-ежзик",
])
def test_anything_else_is_not_a_code(typed):
    assert recovery_codes.normalise(typed) is None


# ── generating ──────────────────────────────────────────────────────────

async def test_ten_codes_are_made_and_only_their_hashes_are_stored(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        codes = await recovery_codes.generate(db, user_id, NOW)
        stored = (await db.execute(sa.select(UserRecoveryCode))).scalars().all()

        assert len(codes) == len(set(codes)) == 10
        assert len(stored) == 10
        for row in stored:
            assert row.code_hash.startswith("$2") and row.used_at is None
            assert not any(code.replace("-", "") in row.code_hash for code in codes)
        assert await recovery_codes.remaining(db, user_id) == 10


async def test_generating_records_when(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        await recovery_codes.generate(db, user_id, NOW)
        state = await db.get(UserTwoStep, user_id)

        assert state.recovery_codes_generated_at.replace(tzinfo=timezone.utc) == NOW


async def test_generating_again_cancels_the_old_codes(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        old = await recovery_codes.generate(db, user_id, NOW)
        await recovery_codes.use(db, user_id, old[0], NOW)
        new = await recovery_codes.generate(db, user_id, NOW + timedelta(days=1))

        assert not set(old) & set(new)
        assert await recovery_codes.remaining(db, user_id) == 10
        assert await recovery_codes.use(db, user_id, old[1], NOW) is False
        assert await recovery_codes.use(db, user_id, new[0], NOW) is True


# ── using ───────────────────────────────────────────────────────────────

async def test_a_code_works_once(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        codes = await recovery_codes.generate(db, user_id, NOW)

        assert await recovery_codes.use(db, user_id, codes[3], NOW) is True
        assert await recovery_codes.use(db, user_id, codes[3], NOW) is False
        assert await recovery_codes.remaining(db, user_id) == 9


async def test_each_of_the_ten_works(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        codes = await recovery_codes.generate(db, user_id, NOW)

        for code in codes:
            assert await recovery_codes.use(db, user_id, code, NOW) is True
        assert await recovery_codes.remaining(db, user_id) == 0
        assert await recovery_codes.use(db, user_id, codes[0], NOW) is False


async def test_a_code_typed_loosely_is_accepted(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        code = (await recovery_codes.generate(db, user_id, NOW))[0]

        assert await recovery_codes.use(db, user_id, f"  {code.upper().replace('-', ' ')} ", NOW) is True


@pytest.mark.parametrize("typed", ["", "nonsense", "abcde-fghjk", "123456"])
async def test_a_wrong_code_uses_nothing_up(sf, typed):
    async with sf() as db:
        user_id = await seed_user(db)
        await recovery_codes.generate(db, user_id, NOW)

        assert await recovery_codes.use(db, user_id, typed, NOW) is False
        assert await recovery_codes.remaining(db, user_id) == 10


async def test_one_accounts_code_is_not_anothers(sf):
    async with sf() as db:
        first, second = await seed_user(db), await seed_user(db)
        codes = await recovery_codes.generate(db, first, NOW)
        await recovery_codes.generate(db, second, NOW)

        assert await recovery_codes.use(db, second, codes[0], NOW) is False
        assert await recovery_codes.remaining(db, first) == 10
        assert await recovery_codes.use(db, first, codes[0], NOW) is True


async def test_an_account_without_codes_has_none_left(sf):
    async with sf() as db:
        user_id = await seed_user(db)

        assert await recovery_codes.remaining(db, user_id) == 0
        assert await recovery_codes.use(db, user_id, "abcde-fghjk", NOW) is False


# ── clearing ────────────────────────────────────────────────────────────

async def test_clearing_deletes_them_all(sf):
    async with sf() as db:
        user_id, other = await seed_user(db), await seed_user(db)
        codes = await recovery_codes.generate(db, user_id, NOW)
        await recovery_codes.generate(db, other, NOW)
        await recovery_codes.clear(db, user_id)

        assert await recovery_codes.remaining(db, user_id) == 0
        assert await recovery_codes.use(db, user_id, codes[0], NOW) is False
        assert (await db.get(UserTwoStep, user_id)).recovery_codes_generated_at is None
        assert await recovery_codes.remaining(db, other) == 10
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_recovery_codes.py
```

Expected: a collection error, `ImportError: cannot import name 'recovery_codes' from 'app.services.two_step'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/two_step/recovery_codes.py`:

```python
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
```

- [ ] **Step 4: Make the hashes cheap in tests**

Ten codes are hashed at once. At the real cost that is most of a second for every later test that turns two-step verification on.

**`backend/tests/conftest.py`**. Replace:

````python
# Distinguishes "no active token scope at all" (JWT-style session) from an
# explicit Full-access token (active_token_scopes=None).
_UNSET = object()
````

with:

````python
# Distinguishes "no active token scope at all" (JWT-style session) from an
# explicit Full-access token (active_token_scopes=None).
_UNSET = object()


@pytest.fixture(autouse=True)
def _cheap_recovery_code_hashes(monkeypatch):
    """Ten recovery codes are hashed in one go; at the real cost that is
    most of a second for every test that turns two-step verification on."""
    from app.services.two_step import recovery_codes

    monkeypatch.setattr(recovery_codes, "HASH_ROUNDS", 4)
````

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_recovery_codes.py
```

Expected: `29 passed`, in a few seconds.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/two_step/recovery_codes.py backend/tests/conftest.py backend/tests/test_two_step_recovery_codes.py
git commit -m "feat(two-step): one-time recovery codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Tickets and wrong tries

**Files:**
- Create: `backend/app/services/two_step/tickets.py`, `backend/app/services/two_step/attempts.py`
- Test: `backend/tests/test_two_step_tickets.py` (new)

**Interfaces:**
- Consumes: `TwoStepTicket`, `UserTwoStep` (Task 2); `aware`, `ensure_state` (Task 3).
- Produces, in `tickets`: `VERIFY = "verify"`, `SETUP = "setup"`, `REGISTER = "register"`, `SETUP_DONE = "setup_done"`, `LIFETIME` (5, 15 and 5 minutes), `issue(db, user_id, purpose: str, now: datetime) -> str` (the token), `find(db, token: str, purpose: str, now: datetime) -> TwoStepTicket | None`, `set_challenge(db, ticket, challenge: bytes) -> None`, `take_challenge(db, ticket) -> bytes | None`, `mark_setup_done(db, ticket) -> None`, `consume(db, ticket) -> bool`.
- Produces, in `attempts`: `MAX_FAILURES = 5`, `PAUSE = timedelta(minutes=5)`, `Paused(Exception)` with `.seconds` and `.minutes`, `guard(db, user_id, now: datetime) -> None` (raises `Paused`; takes the account's row lock), `failed(db, user_id, now: datetime) -> bool` (True when a pause begins), `succeeded(db, user_id) -> None`. None of them commits.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_tickets.py`:

```python
"""Tickets, and the pause after too many wrong tries."""
import hashlib
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.models.two_step import TwoStepTicket, UserTwoStep
from app.services.two_step import attempts, tickets
from tests._rbac import build_inmemory_factory, seed_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
MINUTE = timedelta(minutes=1)
SECOND = timedelta(seconds=1)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


# ── tickets ─────────────────────────────────────────────────────────────

async def test_a_ticket_is_found_by_its_token_and_purpose(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        token = await tickets.issue(db, user_id, tickets.VERIFY, NOW)
        ticket = await tickets.find(db, token, tickets.VERIFY, NOW)

        assert ticket is not None and ticket.user_id == user_id
        assert len(token) >= 43  # 32 random bytes


async def test_only_the_hash_of_the_token_is_stored(sf):
    async with sf() as db:
        token = await tickets.issue(db, await seed_user(db), tickets.VERIFY, NOW)
        stored = (await db.execute(sa.select(TwoStepTicket))).scalar_one()

        assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in stored.token_hash


async def test_a_ticket_is_good_for_one_purpose_only(sf):
    async with sf() as db:
        token = await tickets.issue(db, await seed_user(db), tickets.SETUP, NOW)

        assert await tickets.find(db, token, tickets.VERIFY, NOW) is None
        assert await tickets.find(db, token, tickets.REGISTER, NOW) is None
        assert await tickets.find(db, token, tickets.SETUP, NOW) is not None


@pytest.mark.parametrize("purpose, minutes", [
    (tickets.VERIFY, 5), (tickets.SETUP, 15), (tickets.REGISTER, 5),
])
async def test_a_ticket_expires(sf, purpose, minutes):
    async with sf() as db:
        token = await tickets.issue(db, await seed_user(db), purpose, NOW)
        last_moment = NOW + minutes * MINUTE - SECOND

        assert await tickets.find(db, token, purpose, last_moment) is not None
        assert await tickets.find(db, token, purpose, NOW + minutes * MINUTE) is None


@pytest.mark.parametrize("token", ["", "nonsense", "a" * 43])
async def test_an_unknown_token_finds_nothing(sf, token):
    async with sf() as db:
        await tickets.issue(db, await seed_user(db), tickets.VERIFY, NOW)
        assert await tickets.find(db, token, tickets.VERIFY, NOW) is None


async def test_a_ticket_is_used_up_once(sf):
    async with sf() as db:
        token = await tickets.issue(db, await seed_user(db), tickets.VERIFY, NOW)
        ticket = await tickets.find(db, token, tickets.VERIFY, NOW)

        assert await tickets.consume(db, ticket) is True
        assert await tickets.consume(db, ticket) is False
        assert await tickets.find(db, token, tickets.VERIFY, NOW) is None


async def test_two_browsers_each_keep_their_own_ticket(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        first = await tickets.issue(db, user_id, tickets.VERIFY, NOW)
        second = await tickets.issue(db, user_id, tickets.VERIFY, NOW)

        assert first != second
        assert await tickets.consume(db, await tickets.find(db, first, tickets.VERIFY, NOW))
        assert await tickets.find(db, second, tickets.VERIFY, NOW) is not None


async def test_issuing_clears_out_expired_tickets(sf):
    async with sf() as db:
        user_id, other = await seed_user(db), await seed_user(db)
        await tickets.issue(db, user_id, tickets.VERIFY, NOW)
        await tickets.issue(db, other, tickets.SETUP, NOW)
        await tickets.issue(db, other, tickets.VERIFY, NOW + 10 * MINUTE)
        left = (await db.execute(sa.select(TwoStepTicket.purpose))).scalars().all()

        # The 5-minute ticket is gone; the 15-minute one and the new one remain.
        assert sorted(left) == ["setup", "verify"]


async def test_issuing_works_while_an_earlier_ticket_is_in_hand(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        first = await tickets.issue(db, user_id, tickets.VERIFY, NOW)
        ticket = await tickets.find(db, first, tickets.VERIFY, NOW)
        await db.refresh(ticket)  # as the database hands it back

        second = await tickets.issue(db, user_id, tickets.VERIFY, NOW + MINUTE)

        assert await tickets.find(db, second, tickets.VERIFY, NOW + MINUTE) is not None
        assert await tickets.find(db, first, tickets.VERIFY, NOW + MINUTE) is not None


async def test_a_setup_ticket_that_has_done_its_work_only_finishes(sf):
    async with sf() as db:
        token = await tickets.issue(db, await seed_user(db), tickets.SETUP, NOW)
        ticket = await tickets.find(db, token, tickets.SETUP, NOW)
        await tickets.mark_setup_done(db, ticket)

        assert await tickets.find(db, token, tickets.SETUP, NOW) is None
        assert await tickets.find(db, token, tickets.SETUP_DONE, NOW) is not None
        # It keeps the time it was given.
        assert await tickets.find(db, token, tickets.SETUP_DONE, NOW + 15 * MINUTE) is None


async def test_a_challenge_is_good_for_one_answer(sf):
    async with sf() as db:
        token = await tickets.issue(db, await seed_user(db), tickets.VERIFY, NOW)
        ticket = await tickets.find(db, token, tickets.VERIFY, NOW)

        assert await tickets.take_challenge(db, ticket) is None
        await tickets.set_challenge(db, ticket, b"first")
        await tickets.set_challenge(db, ticket, b"second")

        assert await tickets.take_challenge(db, ticket) == b"second"
        assert await tickets.take_challenge(db, ticket) is None
        # The ticket itself is still there for another try.
        assert await tickets.find(db, token, tickets.VERIFY, NOW) is not None


# ── wrong tries ─────────────────────────────────────────────────────────

async def test_four_wrong_tries_do_not_pause(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        for _ in range(4):
            await attempts.guard(db, user_id, NOW)
            assert await attempts.failed(db, user_id, NOW) is False

        await attempts.guard(db, user_id, NOW)


async def test_the_fifth_wrong_try_starts_a_five_minute_pause(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        for _ in range(4):
            await attempts.failed(db, user_id, NOW)
        assert await attempts.failed(db, user_id, NOW) is True

        with pytest.raises(attempts.Paused) as paused:
            await attempts.guard(db, user_id, NOW + SECOND)
        assert paused.value.seconds == 299 and paused.value.minutes == 5

        with pytest.raises(attempts.Paused) as paused:
            await attempts.guard(db, user_id, NOW + 4 * MINUTE + 30 * SECOND)
        assert paused.value.minutes == 1


async def test_the_pause_ends_on_time_and_counting_starts_again(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        for _ in range(5):
            await attempts.failed(db, user_id, NOW)
        later = NOW + 5 * MINUTE

        await attempts.guard(db, user_id, later)
        state = await db.get(UserTwoStep, user_id)
        assert state.failed_attempts == 0 and state.locked_until is None

        for _ in range(4):
            assert await attempts.failed(db, user_id, later) is False
        assert await attempts.failed(db, user_id, later) is True


async def test_a_right_answer_forgets_the_wrong_ones(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        for _ in range(4):
            await attempts.failed(db, user_id, NOW)
        await attempts.succeeded(db, user_id)

        for _ in range(4):
            assert await attempts.failed(db, user_id, NOW) is False
        await attempts.guard(db, user_id, NOW)


async def test_one_accounts_pause_is_not_anothers(sf):
    async with sf() as db:
        first, second = await seed_user(db), await seed_user(db)
        for _ in range(5):
            await attempts.failed(db, first, NOW)

        await attempts.guard(db, second, NOW)
        with pytest.raises(attempts.Paused):
            await attempts.guard(db, first, NOW)


async def test_guarding_an_account_that_never_tried_is_fine(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        await attempts.guard(db, user_id, NOW)
        assert (await db.get(UserTwoStep, user_id)).failed_attempts == 0
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_tickets.py
```

Expected: a collection error, `ImportError: cannot import name 'attempts' from 'app.services.two_step'`.

- [ ] **Step 3: Write the two modules**

Create `backend/app/services/two_step/tickets.py`:

```python
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
```

Create `backend/app/services/two_step/attempts.py`:

```python
"""Wrong tries at the second step, and the pause after too many.

Kept in the database and not in Redis: the request rate limiter lets every
request through when Redis cannot be reached. Reaching the second step at all
needs the account's password, so this cannot lock someone else out.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import UserTwoStep
from app.services.two_step.state import aware, ensure_state

MAX_FAILURES = 5
PAUSE = timedelta(minutes=5)


class Paused(Exception):
    """Too many wrong tries: nothing is checked until the pause is over."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"paused for {seconds}s")
        self.seconds = seconds

    @property
    def minutes(self) -> int:
        return max(1, math.ceil(self.seconds / 60))


async def guard(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> None:
    """Call before checking an answer. Raises ``Paused`` during a pause.

    Takes the account's row lock for the rest of the transaction, so tries
    for one account are checked one at a time and the count is exact.
    """
    await ensure_state(db, user_id)
    state = (
        await db.execute(
            select(UserTwoStep)
            .where(UserTwoStep.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    until = aware(state.locked_until)
    if until is None:
        return
    if until > now:
        raise Paused(math.ceil((until - now).total_seconds()))
    # The pause is over: start counting again.
    state.locked_until = None
    state.failed_attempts = 0
    await db.flush()


async def failed(db: AsyncSession, user_id: uuid.UUID, now: datetime) -> bool:
    """Count a wrong try. True when it was one too many and a pause begins."""
    state = await ensure_state(db, user_id)
    state.failed_attempts = (state.failed_attempts or 0) + 1
    paused = state.failed_attempts >= MAX_FAILURES
    if paused:
        state.locked_until = now + PAUSE
    await db.flush()
    return paused


async def succeeded(db: AsyncSession, user_id: uuid.UUID) -> None:
    """A right answer: forget the wrong ones."""
    state = await ensure_state(db, user_id)
    state.failed_attempts = 0
    state.locked_until = None
    await db.flush()
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_tickets.py
```

Expected: `21 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/two_step/tickets.py backend/app/services/two_step/attempts.py backend/tests/test_two_step_tickets.py
git commit -m "feat(two-step): sign-in tickets and the pause after wrong tries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Passkeys

**Files:**
- Create: `backend/app/services/two_step/passkeys.py`
- Test: `backend/tests/_passkey.py` (new), `backend/tests/test_two_step_passkeys.py` (new)

**Interfaces:**
- Consumes: `UserPasskey` (Task 2); `get_settings().MEGOOCI_PUBLIC_URL`; the `webauthn` library.
- Produces: `MAX_PASSKEYS = 10`, `origin() -> str`, `rp_id() -> str`, `clean_name(name: str | None) -> str`, `PasskeyError(Exception)` with `.counter_went_back: bool`, `TooManyPasskeys(Exception)`, `AlreadyRegistered(Exception)`, `list_for(db, user_id) -> list[UserPasskey]`, `count(db, user_id) -> int`, `registration_options(db, user: User) -> tuple[dict, bytes]` (options as JSON, challenge), `register(db, user_id, credential: dict, challenge: bytes, name: str | None, now: datetime) -> UserPasskey`, `authentication_options(db, user_id) -> tuple[dict, bytes]`, `verify(db, user_id, credential: dict, challenge: bytes, now: datetime) -> UserPasskey`, `rename(db, user_id, passkey_id, name: str) -> UserPasskey | None`, `remove(db, user_id, passkey_id) -> bool`. None of them commits.
- Produces, for tests: `SoftwareAuthenticator(origin=…, rp_id=…, counts=True)` in `tests/_passkey.py` with `.credential_id`, `.sign_count`, `.create(options) -> dict` and `.get(options) -> dict`, and `b64url(data: bytes) -> str`.

- [ ] **Step 1: Write the software authenticator and the failing tests**

Create `backend/tests/_passkey.py`:

```python
"""A passkey authenticator in software, for tests.

It does what a browser and a security key do together: makes a real P-256 key
pair, and answers the server's options with real, signed WebAuthn responses.
The server's checks therefore run for real, on good answers and bad ones.
"""
import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ORIGIN = "http://localhost:3000"  # the default MEGOOCI_PUBLIC_URL
RP_ID = "localhost"

_USER_PRESENT = 0x01
_USER_VERIFIED = 0x04
_HAS_CREDENTIAL = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftwareAuthenticator:
    def __init__(self, *, origin: str = ORIGIN, rp_id: str = RP_ID, counts: bool = True) -> None:
        self.origin = origin
        self.rp_id = rp_id
        # Some authenticators (most phones and laptops) always report 0.
        self.counts = counts
        self.sign_count = 0
        self.credential_id = os.urandom(32)
        self._key = ec.generate_private_key(ec.SECP256R1())

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        ).encode()

    def _cose_key(self) -> bytes:
        numbers = self._key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,    # kty: EC2
            3: -7,   # alg: ES256
            -1: 1,   # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def _authenticator_data(self, *, with_credential: bool) -> bytes:
        flags = _USER_PRESENT | _USER_VERIFIED | (_HAS_CREDENTIAL if with_credential else 0)
        data = hashlib.sha256(self.rp_id.encode()).digest()
        data += bytes([flags]) + self.sign_count.to_bytes(4, "big")
        if with_credential:
            data += bytes(16)  # aaguid
            data += len(self.credential_id).to_bytes(2, "big") + self.credential_id
            data += self._cose_key()
        return data

    def create(self, options: dict) -> dict:
        """The answer to registration options (``navigator.credentials.create``)."""
        attestation = cbor2.dumps({
            "fmt": "none",
            "attStmt": {},
            "authData": self._authenticator_data(with_credential=True),
        })
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", options["challenge"])),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
        }

    def get(self, options: dict) -> dict:
        """The answer to authentication options (``navigator.credentials.get``)."""
        if self.counts:
            self.sign_count += 1
        authenticator_data = self._authenticator_data(with_credential=False)
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = self._key.sign(
            authenticator_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(authenticator_data),
                "signature": b64url(signature),
            },
        }
```

Create `backend/tests/test_two_step_passkeys.py`:

```python
"""Passkeys: registering one and signing in with it, checked for real with a
software authenticator, on good answers and bad ones."""
import copy
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.config import get_settings
from app.models.two_step import UserPasskey
from app.models.user import User
from app.services.two_step import passkeys
from tests._passkey import SoftwareAuthenticator, b64url
from tests._rbac import build_inmemory_factory, seed_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _user(db) -> User:
    return await db.get(User, await seed_user(db))


async def _register(db, user, device=None, name="Laptop") -> tuple[SoftwareAuthenticator, UserPasskey]:
    device = device or SoftwareAuthenticator()
    options, challenge = await passkeys.registration_options(db, user)
    passkey = await passkeys.register(db, user.id, device.create(options), challenge, name, NOW)
    return device, passkey


async def _sign_in(db, user, device) -> UserPasskey:
    options, challenge = await passkeys.authentication_options(db, user.id)
    return await passkeys.verify(db, user.id, device.get(options), challenge, NOW)


# ── where passkeys work ─────────────────────────────────────────────────

@pytest.mark.parametrize("public_url, origin, rp_id", [
    ("http://localhost:3000", "http://localhost:3000", "localhost"),
    ("https://ci.example.com", "https://ci.example.com", "ci.example.com"),
    ("https://CI.Example.com/", "https://ci.example.com", "ci.example.com"),
    ("https://ci.example.com:443/app", "https://ci.example.com", "ci.example.com"),
    ("https://ci.example.com:8443", "https://ci.example.com:8443", "ci.example.com"),
    ("http://ci.example.com:80", "http://ci.example.com", "ci.example.com"),
])
def test_the_origin_and_relying_party_come_from_the_public_url(monkeypatch, public_url, origin, rp_id):
    monkeypatch.setattr(get_settings(), "MEGOOCI_PUBLIC_URL", public_url)
    assert (passkeys.origin(), passkeys.rp_id()) == (origin, rp_id)


# ── registering ─────────────────────────────────────────────────────────

async def test_the_options_ask_for_a_second_step_passkey(sf):
    async with sf() as db:
        user = await _user(db)
        options, challenge = await passkeys.registration_options(db, user)

        assert options["rp"] == {"name": "MegooCI", "id": "localhost"}
        assert options["user"]["name"] == user.email
        assert options["user"]["id"] == b64url(user.id.bytes)
        assert options["challenge"] == b64url(challenge) and len(challenge) >= 32
        assert options["attestation"] == "none"
        assert options["authenticatorSelection"]["userVerification"] == "preferred"
        assert options["authenticatorSelection"]["residentKey"] == "discouraged"
        assert options["excludeCredentials"] == []


async def test_a_passkey_is_registered_with_its_public_key_and_name(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user, name="  Work   laptop ")

        assert passkey.user_id == user.id and passkey.name == "Work laptop"
        assert bytes(passkey.credential_id) == device.credential_id
        assert passkey.transports == ["internal"]
        assert passkey.sign_count == 0 and passkey.last_used_at is None
        assert [p.id for p in await passkeys.list_for(db, user.id)] == [passkey.id]


@pytest.mark.parametrize("name, stored", [("", "Passkey"), (None, "Passkey"), ("   ", "Passkey"), ("x" * 300, "x" * 100)])
async def test_a_missing_or_overlong_name_is_tidied(sf, name, stored):
    async with sf() as db:
        _, passkey = await _register(db, await _user(db), name=name)
        assert passkey.name == stored


async def test_an_answer_to_another_challenge_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        device = SoftwareAuthenticator()
        options, _ = await passkeys.registration_options(db, user)
        _, another_challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, device.create(options), another_challenge, "x", NOW)
        assert await passkeys.count(db, user.id) == 0


async def test_an_answer_from_another_site_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        phishing = SoftwareAuthenticator(origin="https://megooci.evil.example")
        options, challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, phishing.create(options), challenge, "x", NOW)


async def test_an_answer_for_another_relying_party_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        device = SoftwareAuthenticator(rp_id="other.example")
        options, challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, device.create(options), challenge, "x", NOW)


@pytest.mark.parametrize("answer", [
    {},
    {"id": "x", "rawId": "x", "type": "public-key"},
    {"id": "x", "rawId": "x", "type": "public-key", "response": {}},
    {"id": "x", "rawId": "x", "type": "public-key",
     "response": {"clientDataJSON": "!!!", "attestationObject": "!!!"}},
    {"id": 1, "rawId": None, "type": "public-key", "response": "nonsense"},
])
async def test_a_malformed_answer_is_refused_not_a_crash(sf, answer):
    async with sf() as db:
        user = await _user(db)
        _, challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, answer, challenge, "x", NOW)


async def test_the_same_passkey_cannot_be_registered_twice(sf):
    async with sf() as db:
        user, other = await _user(db), await _user(db)
        device, _ = await _register(db, user)

        options, challenge = await passkeys.registration_options(db, user)
        assert options["excludeCredentials"][0]["id"] == b64url(device.credential_id)
        with pytest.raises(passkeys.AlreadyRegistered):
            await passkeys.register(db, user.id, device.create(options), challenge, "again", NOW)

        # ...nor by another account.
        options, challenge = await passkeys.registration_options(db, other)
        with pytest.raises(passkeys.AlreadyRegistered):
            await passkeys.register(db, other.id, device.create(options), challenge, "theirs", NOW)


async def test_an_account_holds_ten_passkeys_at_most(sf):
    async with sf() as db:
        user = await _user(db)
        for _ in range(9):
            await _register(db, user)
        # The tenth is asked for while there is still room...
        options, challenge = await passkeys.registration_options(db, user)
        await _register(db, user)

        # ...so by the time its answer arrives the account is full.
        with pytest.raises(passkeys.TooManyPasskeys):
            await passkeys.register(db, user.id, SoftwareAuthenticator().create(options), challenge, "x", NOW)
        with pytest.raises(passkeys.TooManyPasskeys):
            await passkeys.registration_options(db, user)
        assert await passkeys.count(db, user.id) == 10


# ── signing in ──────────────────────────────────────────────────────────

async def test_the_sign_in_options_list_the_accounts_passkeys(sf):
    async with sf() as db:
        user = await _user(db)
        first, _ = await _register(db, user)
        second, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)

        assert options["rpId"] == "localhost" and options["userVerification"] == "preferred"
        assert options["challenge"] == b64url(challenge)
        assert {c["id"] for c in options["allowCredentials"]} == {
            b64url(first.credential_id), b64url(second.credential_id)
        }


async def test_a_registered_passkey_signs_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user)

        used = await _sign_in(db, user, device)

        assert used.id == passkey.id and used.sign_count == 1
        assert used.last_used_at.replace(tzinfo=timezone.utc) == NOW
        assert (await _sign_in(db, user, device)).sign_count == 2


async def test_an_authenticator_that_does_not_count_keeps_working(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user, SoftwareAuthenticator(counts=False))

        assert (await _sign_in(db, user, device)).sign_count == 0
        assert (await _sign_in(db, user, device)).sign_count == 0


async def test_a_counter_that_went_back_is_refused_and_marked(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user)
        for _ in range(3):
            await _sign_in(db, user, device)

        # A copy of the key, made when its counter was at 1, is used next.
        device.sign_count = 1
        with pytest.raises(passkeys.PasskeyError) as refused:
            await _sign_in(db, user, device)

        assert refused.value.counter_went_back is True
        assert (await db.get(UserPasskey, passkey.id)).sign_count == 3


async def test_an_answer_to_another_challenge_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user)
        options, _ = await passkeys.authentication_options(db, user.id)
        _, another_challenge = await passkeys.authentication_options(db, user.id)

        with pytest.raises(passkeys.PasskeyError) as refused:
            await passkeys.verify(db, user.id, device.get(options), another_challenge, NOW)

        assert refused.value.counter_went_back is False
        assert (await db.get(UserPasskey, passkey.id)).last_used_at is None


async def test_an_answer_from_another_site_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)
        device.origin = "https://megooci.evil.example"

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, device.get(options), challenge, NOW)


async def test_a_tampered_signature_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)
        answer = device.get(options)
        answer["response"]["signature"] = b64url(b"\x30\x06\x02\x01\x01\x02\x01\x01")

        with pytest.raises(passkeys.PasskeyError) as refused:
            await passkeys.verify(db, user.id, answer, challenge, NOW)
        assert refused.value.counter_went_back is False


async def test_a_passkey_that_is_not_registered_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        await _register(db, user)
        stranger = SoftwareAuthenticator()
        options, challenge = await passkeys.authentication_options(db, user.id)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, stranger.get(options), challenge, NOW)


async def test_one_accounts_passkey_does_not_sign_in_another(sf):
    async with sf() as db:
        owner, other = await _user(db), await _user(db)
        device, _ = await _register(db, owner)
        await _register(db, other)
        options, challenge = await passkeys.authentication_options(db, other.id)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, other.id, device.get(options), challenge, NOW)


async def test_a_key_swapped_for_another_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        impostor = SoftwareAuthenticator()
        impostor.credential_id = device.credential_id  # claims to be the registered one
        options, challenge = await passkeys.authentication_options(db, user.id)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, impostor.get(options), challenge, NOW)


@pytest.mark.parametrize("damage", [
    lambda a: a.clear(),
    lambda a: a.pop("rawId"),
    lambda a: a.update(rawId=12345),
    lambda a: a.update(rawId="!!! not base64 !!!"),
    lambda a: a.pop("response"),
    lambda a: a.update(response="nonsense"),
    lambda a: a["response"].pop("signature"),
    lambda a: a["response"].update(authenticatorData="AAAA"),
    lambda a: a["response"].update(clientDataJSON=b64url(b"not json")),
])
async def test_a_damaged_answer_is_refused_not_a_crash(sf, damage):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)
        answer = copy.deepcopy(device.get(options))
        damage(answer)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, answer, challenge, NOW)


# ── renaming and removing ───────────────────────────────────────────────

async def test_a_passkey_is_renamed_by_its_owner_only(sf):
    async with sf() as db:
        owner, other = await _user(db), await _user(db)
        _, passkey = await _register(db, owner)

        assert await passkeys.rename(db, other.id, passkey.id, "Mine now") is None
        assert (await passkeys.rename(db, owner.id, passkey.id, " Desk  key ")).name == "Desk key"
        assert await passkeys.rename(db, owner.id, uuid.uuid4(), "x") is None


async def test_a_passkey_is_removed_by_its_owner_only(sf):
    async with sf() as db:
        owner, other = await _user(db), await _user(db)
        device, passkey = await _register(db, owner)

        assert await passkeys.remove(db, other.id, passkey.id) is False
        assert await passkeys.remove(db, owner.id, passkey.id) is True
        assert await passkeys.remove(db, owner.id, passkey.id) is False
        assert await passkeys.count(db, owner.id) == 0

        options, challenge = await passkeys.authentication_options(db, owner.id)
        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, owner.id, device.get(options), challenge, NOW)


async def test_passkeys_are_listed_oldest_first(sf):
    async with sf() as db:
        user = await _user(db)
        names = []
        for index in range(3):
            device = SoftwareAuthenticator()
            options, challenge = await passkeys.registration_options(db, user)
            await passkeys.register(
                db, user.id, device.create(options), challenge, f"key {index}",
                NOW + timedelta(minutes=index),
            )
            names.append(f"key {index}")

        assert [p.name for p in await passkeys.list_for(db, user.id)] == names
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_passkeys.py
```

Expected: a collection error, `ImportError: cannot import name 'passkeys' from 'app.services.two_step'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/two_step/passkeys.py`:

```python
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_passkeys.py
```

Expected: `44 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/two_step/passkeys.py backend/tests/_passkey.py backend/tests/test_two_step_passkeys.py
git commit -m "feat(two-step): passkeys as the second step

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: What an account has, and what a correct password gets

**Files:**
- Create: `backend/app/services/two_step/status.py`, `backend/app/services/two_step/sign_in.py`, `backend/app/services/two_step/reset.py`, `backend/app/services/two_step/clock.py`
- Test: `backend/tests/_two_step.py` (new), `backend/tests/test_two_step_sign_in.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 3 to 6; `SystemSetting`; `create_access_token`, `create_refresh_token`.
- Produces, in `status`: `REQUIRE_KEY = "require_two_step"`, `methods(db, user_id) -> list[str]` (`"totp"` and/or `"passkey"`, in that order), `is_on(db, user_id) -> bool`, `is_required(db) -> bool`, `set_required(db, required: bool) -> None`, `setup_required(db, user_id) -> bool`, `enabled_user_ids(db) -> set[UUID]`, `counts(db) -> tuple[int, int]` (active users; those of them with it on), `after_method_added(db, user_id, now) -> list[str] | None` (ten codes when it was the first method), `after_method_removed(db, user_id) -> None`, `summary(db, user_id) -> dict`.
- Produces, in `sign_in`: `SIGNED_IN`, `TWO_STEP_REQUIRED`, `SETUP_REQUIRED`, `session(user: User) -> dict` (`status`, `access_token`, `refresh_token`, `token_type`), `decide(db, user: User, now: datetime) -> dict`, `may_renew(db, user_id) -> bool`.
- Produces: `reset.reset(db, user_id) -> bool` (whether it was on) and `clock.now() -> datetime`.
- Produces, for tests, in `tests/_two_step.py`: `NOW`, `STEP`, `PASSWORD`, `cheap_hash(password) -> str`, `code_at(secret, when) -> str`, `add_user(db, *, password=PASSWORD, is_admin=False, is_active=True, name=…) -> User`, `turn_on_totp(db, user, now=NOW) -> tuple[str, list[str] | None]`, `add_passkey(db, user, now=NOW, *, name="Laptop") -> tuple[SoftwareAuthenticator, list[str] | None]`.

- [ ] **Step 1: Write the test helpers and the failing tests**

Create `backend/tests/_two_step.py`:

```python
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
```

Create `backend/tests/test_two_step_sign_in.py`:

```python
"""What an account has, what a correct password gets, and the reset."""
import os

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.core.security import decode_token
from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.services.two_step import attempts, passkeys, recovery_codes, sign_in, status, tickets, totp
from app.services.two_step.reset import reset
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, STEP, add_passkey, add_user, code_at, turn_on_totp


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


# ── what an account has ─────────────────────────────────────────────────

async def test_a_new_account_has_nothing(sf):
    async with sf() as db:
        user = await add_user(db)

        assert await status.methods(db, user.id) == []
        assert await status.is_on(db, user.id) is False


async def test_an_unconfirmed_authenticator_does_not_count(sf):
    async with sf() as db:
        user = await add_user(db)
        await totp.begin(db, user)

        assert await status.is_on(db, user.id) is False


async def test_each_kind_of_method_turns_it_on(sf):
    async with sf() as db:
        with_app, with_key, with_both = await add_user(db), await add_user(db), await add_user(db)
        await turn_on_totp(db, with_app)
        await add_passkey(db, with_key)
        await add_passkey(db, with_both)
        await turn_on_totp(db, with_both)

        assert await status.methods(db, with_app.id) == ["totp"]
        assert await status.methods(db, with_key.id) == ["passkey"]
        assert await status.methods(db, with_both.id) == ["totp", "passkey"]
        assert await status.enabled_user_ids(db) == {with_app.id, with_key.id, with_both.id}


async def test_the_first_method_brings_ten_recovery_codes_and_the_second_none(sf):
    async with sf() as db:
        user = await add_user(db)
        _, first = await turn_on_totp(db, user)
        _, second = await add_passkey(db, user)

        assert len(first) == 10 and second is None
        assert await recovery_codes.remaining(db, user.id) == 10


async def test_removing_one_of_two_methods_keeps_the_codes(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await add_passkey(db, user)
        await totp.remove(db, user.id)
        await status.after_method_removed(db, user.id)

        assert await status.methods(db, user.id) == ["passkey"]
        assert await recovery_codes.remaining(db, user.id) == 10


async def test_removing_the_last_method_deletes_the_codes(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await totp.remove(db, user.id)
        await status.after_method_removed(db, user.id)

        assert await status.is_on(db, user.id) is False
        assert await recovery_codes.remaining(db, user.id) == 0
        # Turning it on again gives new codes.
        _, codes = await turn_on_totp(db, user)
        assert len(codes) == 10


async def test_the_requirement_is_off_until_switched_on(sf):
    async with sf() as db:
        user = await add_user(db)
        assert await status.is_required(db) is False
        assert await status.setup_required(db, user.id) is False

        await status.set_required(db, True)
        assert await status.is_required(db) is True
        assert await status.setup_required(db, user.id) is True

        await turn_on_totp(db, user)
        assert await status.setup_required(db, user.id) is False

        await status.set_required(db, False)
        assert await status.is_required(db) is False


async def test_counts_cover_active_users_only(sf):
    async with sf() as db:
        on, off, gone = await add_user(db), await add_user(db), await add_user(db, is_active=False)
        await turn_on_totp(db, on)
        await turn_on_totp(db, gone)

        assert await status.counts(db) == (2, 1)
        assert off.id not in await status.enabled_user_ids(db)


async def test_the_summary_shows_what_there_is_and_no_secret(sf):
    async with sf() as db:
        user = await add_user(db)
        empty = await status.summary(db, user.id)
        assert empty == {
            "enabled": False, "required": False, "totp": None, "passkeys": [],
            "recovery_codes": None, "passkey_origin": "http://localhost:3000",
        }

        secret, codes = await turn_on_totp(db, user)
        device, _ = await add_passkey(db, user, name="YubiKey")
        await recovery_codes.use(db, user.id, codes[0], NOW)
        full = await status.summary(db, user.id)

        assert full["enabled"] is True
        assert full["totp"]["confirmed_at"] is not None
        assert [(key["name"], key["last_used_at"]) for key in full["passkeys"]] == [("YubiKey", None)]
        assert full["recovery_codes"] == {"left": 9, "generated_at": NOW}
        text = repr(full)
        assert secret not in text and device.credential_id.hex() not in text
        assert not any(code in text for code in codes)


# ── what a correct password gets ────────────────────────────────────────

async def test_without_two_step_a_password_signs_in(sf):
    async with sf() as db:
        user = await add_user(db)
        outcome = await sign_in.decide(db, user, NOW)

        assert outcome["status"] == "signed_in" and outcome["token_type"] == "bearer"
        assert decode_token(outcome["access_token"])["sub"] == str(user.id)
        assert decode_token(outcome["refresh_token"])["type"] == "refresh"
        assert (await db.execute(sa.select(TwoStepTicket))).scalars().all() == []


async def test_with_a_method_a_password_gets_a_ticket_and_no_session(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await add_passkey(db, user)
        outcome = await sign_in.decide(db, user, NOW)

        assert outcome["status"] == "two_step_required"
        assert outcome["methods"] == ["totp", "passkey"]
        assert outcome["passkey_origin"] == "http://localhost:3000"
        assert "access_token" not in outcome and "refresh_token" not in outcome
        ticket = await tickets.find(db, outcome["ticket"], tickets.VERIFY, NOW)
        assert ticket.user_id == user.id


async def test_a_ticket_is_not_a_session(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        outcome = await sign_in.decide(db, user, NOW)

        with pytest.raises(ValueError):
            decode_token(outcome["ticket"])


async def test_when_required_an_account_without_a_method_gets_a_setup_ticket(sf):
    async with sf() as db:
        user = await add_user(db)
        await status.set_required(db, True)
        outcome = await sign_in.decide(db, user, NOW)

        assert outcome["status"] == "two_step_setup_required"
        assert "access_token" not in outcome and "methods" not in outcome
        assert await tickets.find(db, outcome["ticket"], tickets.SETUP, NOW) is not None
        assert await tickets.find(db, outcome["ticket"], tickets.VERIFY, NOW) is None


async def test_when_required_an_account_with_a_method_gets_the_second_step(sf):
    async with sf() as db:
        user = await add_user(db)
        await add_passkey(db, user)
        await status.set_required(db, True)

        assert (await sign_in.decide(db, user, NOW))["status"] == "two_step_required"


async def test_a_session_renews_unless_setup_is_owed(sf):
    async with sf() as db:
        without, with_method = await add_user(db), await add_user(db)
        await turn_on_totp(db, with_method)

        assert await sign_in.may_renew(db, without.id) is True
        await status.set_required(db, True)
        assert await sign_in.may_renew(db, without.id) is False
        assert await sign_in.may_renew(db, with_method.id) is True


# ── the reset ───────────────────────────────────────────────────────────

async def test_a_reset_removes_everything_of_that_account_only(sf):
    async with sf() as db:
        user, other = await add_user(db), await add_user(db)
        for account in (user, other):
            await turn_on_totp(db, account)
            await add_passkey(db, account)
            await sign_in.decide(db, account, NOW)
        for _ in range(5):
            await attempts.failed(db, user.id, NOW)

        assert await reset(db, user.id) is True

        assert await status.methods(db, user.id) == []
        assert await recovery_codes.remaining(db, user.id) == 0
        await attempts.guard(db, user.id, NOW)  # the pause is gone too
        for model in (UserPasskey, UserRecoveryCode, TwoStepTicket):
            owners = set((await db.execute(sa.select(model.user_id))).scalars())
            assert owners == {other.id}, model.__name__
        assert await status.methods(db, other.id) == ["totp", "passkey"]


async def test_after_a_reset_the_old_code_and_passkey_no_longer_work(sf):
    async with sf() as db:
        user = await add_user(db)
        secret, codes = await turn_on_totp(db, user)
        device, _ = await add_passkey(db, user)
        await reset(db, user.id)

        assert await totp.check(db, user.id, code_at(secret, NOW + STEP), NOW + STEP) is False
        assert await recovery_codes.use(db, user.id, codes[0], NOW) is False
        options, challenge = await passkeys.authentication_options(db, user.id)
        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, device.get(options), challenge, NOW)
        assert await sign_in.may_renew(db, user.id) is True


async def test_resetting_an_account_with_nothing_is_harmless(sf):
    async with sf() as db:
        user = await add_user(db)
        assert await reset(db, user.id) is False
        assert (await db.execute(sa.select(UserTwoStep))).scalars().all() == []
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_sign_in.py
```

Expected: a collection error, `ImportError: cannot import name 'sign_in' from 'app.services.two_step'`.

- [ ] **Step 3: Write the four modules**

Create `backend/app/services/two_step/clock.py`:

```python
"""The time, read in one place so that tests can set it."""

from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)
```

Create `backend/app/services/two_step/status.py`:

```python
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
```

Create `backend/app/services/two_step/sign_in.py`:

```python
"""What a user gets once their password has been accepted.

One place decides, for login, sign-up and an accepted invitation alike:

- ``signed_in``                the session, for an account without two-step
                               verification on a server that does not require it
- ``two_step_required``        a ticket for the second step
- ``two_step_setup_required``  a ticket for the forced setup
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token, create_refresh_token
from app.models.user import User
from app.services.two_step import passkeys, status, tickets

SIGNED_IN = "signed_in"
TWO_STEP_REQUIRED = "two_step_required"
SETUP_REQUIRED = "two_step_setup_required"


def session(user: User) -> dict[str, Any]:
    """The tokens of a signed-in session. Only call this for a user who has
    proved everything their account asks for."""
    token_data = {"sub": str(user.id)}
    return {
        "status": SIGNED_IN,
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


async def decide(db: AsyncSession, user: User, now: datetime) -> dict[str, Any]:
    """The answer to a correct password."""
    kinds = await status.methods(db, user.id)
    if kinds:
        return {
            "status": TWO_STEP_REQUIRED,
            "ticket": await tickets.issue(db, user.id, tickets.VERIFY, now),
            "methods": kinds,
            "passkey_origin": passkeys.origin(),
        }
    if await status.is_required(db):
        return {
            "status": SETUP_REQUIRED,
            "ticket": await tickets.issue(db, user.id, tickets.SETUP, now),
            "passkey_origin": passkeys.origin(),
        }
    return session(user)


async def may_renew(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Whether a session may renew itself. Not when two-step verification is
    required and the account still has none: a session renews every time it
    is used, so it would otherwise never have to be set up."""
    return not await status.setup_required(db, user_id)
```

Create `backend/app/services/two_step/reset.py`:

```python
"""Remove everything an account has for two-step verification.

For a user who has lost the phone, the passkeys and the recovery codes: an
administrator resets them, and they set it up again.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.services.two_step import status


async def reset(db: AsyncSession, user_id: uuid.UUID) -> bool:
    """Remove the account's authenticator, passkeys, recovery codes, pending
    tickets and any pause from wrong tries. Returns whether two-step
    verification was on. Does not end the user's sessions."""
    was_on = await status.is_on(db, user_id)
    for model in (UserPasskey, UserRecoveryCode, TwoStepTicket, UserTwoStep):
        await db.execute(
            delete(model)
            .where(model.user_id == user_id)
            .execution_options(synchronize_session="fetch")
        )
    await db.flush()
    return was_on
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_sign_in.py
```

Expected: `18 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/two_step/status.py backend/app/services/two_step/sign_in.py backend/app/services/two_step/reset.py backend/app/services/two_step/clock.py backend/tests/_two_step.py backend/tests/test_two_step_sign_in.py
git commit -m "feat(two-step): what a correct password gets, and the reset

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Signing in — the routes

**Files:**
- Create: `backend/app/schemas/two_step.py`, `backend/app/api/v1/two_step.py`
- Modify: `backend/app/schemas/auth.py`, `backend/app/api/v1/auth.py`, `backend/app/api/v1/invites.py`, `backend/app/api/v1/router.py`
- Test: `backend/tests/test_two_step_api_sign_in.py` (new)

**Interfaces:**
- Consumes: Tasks 3 to 7; `create_reset_token` in `auth.py`'s imports (Task 1); `rate_limit` from `app.core.rate_limit`; `record` from `app.core.audit`.
- Produces: the models in `app.schemas.two_step` (`SignInResponse`, `TicketRequest`, `VerifyRequest`, `TicketCodeRequest`, `PasskeyAnswer`, `NamedPasskeyAnswer`, `PasskeyOptions`, `TotpSetup`, `RecoveryCodes`, `CodeRequest`, `PasswordRequest`, `RenameRequest`, `PasskeyInfo`, `PasskeyAdded`, `TwoStepSummary`, `SecuritySettings`, `SecuritySettingsUpdate`).
- Produces, in `app.api.v1.two_step`: `router`; the route functions `verify(body, db)`, `passkey_options(body, db)`, `passkey_verify(body, db)`, `setup_totp(body, db)`, `setup_totp_confirm(body, db)`, `setup_passkey_options(body, db)`, `setup_passkey(body, db)`, `setup_finish(body, db)`; and, for the next task, `WRONG_CODE`, `WRONG_PASSKEY`, `TICKET_GONE` and `audit(db, action: str, user_id, **metadata) -> None`.
- Produces: `POST /auth/login`, `POST /auth/signup` and `POST /invites/accept` return a `SignInResponse` with `status`; `GET /auth/me` and `PUT /auth/update-profile` gain `two_step_enabled` and `two_step_setup_required`; `POST /auth/refresh` answers 401 while setup is owed.
- A wrong answer commits its count before it raises: the `get_db` dependency rolls back on an exception, which would otherwise forget it.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_api_sign_in.py`:

```python
"""Signing in with two-step verification: the password step, the second step
and the forced setup, through the routes."""
import hashlib
import os
import sys
import uuid
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import auth, invites, two_step
from app.config import get_settings
from app.core.deps import get_current_user
from app.core.security import create_refresh_token, decode_token
from app.models.audit import AuditLogEntry
from app.models.invite import Invite
from app.models.role import Role
from app.models.two_step import TwoStepTicket
from app.schemas.auth import LoginRequest, RefreshRequest, SignupRequest
from app.schemas.invites import AcceptInviteRequest
from app.schemas.two_step import (
    NamedPasskeyAnswer,
    PasskeyAnswer,
    SignInResponse,
    TicketCodeRequest,
    TicketRequest,
    VerifyRequest,
)
from app.services.two_step import recovery_codes, sign_in, status
from app.services.two_step.reset import reset
from tests._passkey import SoftwareAuthenticator
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, PASSWORD, STEP, add_passkey, add_user, code_at, turn_on_totp

MINUTE = timedelta(minutes=1)


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    # Audit entries written outside the request's transaction go to the same database.
    monkeypatch.setattr("app.core.audit.async_session", factory)
    yield factory
    await engine.dispose()


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr("app.services.two_step.clock.now", lambda: clock.now)
    return clock


async def _password_step(db, user, clock) -> dict:
    """What ``/auth/login`` returns for a correct password, at the test's time."""
    outcome = await sign_in.decide(db, user, clock.now)
    await db.commit()
    return outcome


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value


async def _audit(sf) -> list[tuple[str, dict | None]]:
    async with sf() as db:
        rows = (await db.execute(sa.select(AuditLogEntry))).scalars().all()
        return sorted(((row.action, row.metadata_json) for row in rows), key=lambda item: item[0])


def _session_of(outcome: dict, user) -> None:
    assert outcome["status"] == "signed_in"
    assert decode_token(outcome["access_token"])["sub"] == str(user.id)
    assert decode_token(outcome["refresh_token"])["type"] == "refresh"


# ── the routes ──────────────────────────────────────────────────────────

def test_every_route_is_public_and_takes_a_ticket():
    def calls(dependant) -> set:
        found = set()
        for dependency in dependant.dependencies:
            found.add(dependency.call)
            found |= calls(dependency)
        return found

    assert len(two_step.router.routes) == 8
    for route in two_step.router.routes:
        assert route.methods == {"POST"}, route.path
        assert get_current_user not in calls(route.dependant), route.path
        assert "ticket" in route.body_field.field_info.annotation.model_fields, route.path


def test_they_are_mounted_under_auth_two_step():
    from app.api.v1.router import api_v1_router

    paths = {route.path for route in api_v1_router.routes}
    assert {"/auth/two-step/verify", "/auth/two-step/passkey/options",
            "/auth/two-step/setup/finish"} <= paths


# ── the password step ───────────────────────────────────────────────────

async def test_login_without_two_step_signs_in_as_before(sf):
    async with sf() as db:
        user = await add_user(db)
        await db.commit()  # the sign-in is recorded from a session of its own
        outcome = await auth.login(LoginRequest(email=user.email, password=PASSWORD), db)

        _session_of(outcome, user)
        body = SignInResponse(**outcome).model_dump(exclude_none=True)
        assert set(body) == {"status", "access_token", "refresh_token", "token_type"}
    assert [action for action, _ in await _audit(sf)] == ["login_success"]


async def test_login_with_two_step_gives_a_ticket_and_no_session(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        outcome = await auth.login(LoginRequest(email=user.email, password=PASSWORD), db)

        body = SignInResponse(**outcome).model_dump(exclude_none=True)
        assert set(body) == {"status", "ticket", "methods", "passkey_origin"}
        assert body["status"] == "two_step_required" and body["methods"] == ["totp"]
    # Nobody has signed in yet.
    assert await _audit(sf) == []


async def test_login_when_required_and_not_set_up_gives_a_setup_ticket(sf):
    async with sf() as db:
        user = await add_user(db)
        await status.set_required(db, True)
        outcome = await auth.login(LoginRequest(email=user.email, password=PASSWORD), db)

        body = SignInResponse(**outcome).model_dump(exclude_none=True)
        assert set(body) == {"status", "ticket", "passkey_origin"}
        assert body["status"] == "two_step_setup_required"


async def test_a_wrong_password_gets_no_ticket(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await db.commit()

        refused = await _refused(auth.login(LoginRequest(email=user.email, password="wrong"), db))

        assert refused.status_code == 401
        assert (await db.execute(sa.select(TwoStepTicket))).scalars().all() == []


# ── the second step: a code ─────────────────────────────────────────────

async def test_a_right_code_signs_in_and_uses_the_ticket_up(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        outcome = await two_step.verify(VerifyRequest(ticket=ticket, code=code_at(secret, NOW)), db)
        await db.commit()

        _session_of(outcome, user)
        assert "recovery_codes_left" not in outcome
        clock.advance(STEP)
        again = await _refused(two_step.verify(
            VerifyRequest(ticket=ticket, code=code_at(secret, clock.now)), db))
        assert (again.status_code, again.detail) == (410, "That took too long. Sign in again.")
    assert ("login_success", {"second_step": "totp"}) in await _audit(sf)


async def test_a_wrong_code_is_refused_and_the_ticket_stays(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        refused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="000000"), db))
        assert (refused.status_code, refused.detail) == (401, "That code is not right.")

        outcome = await two_step.verify(VerifyRequest(ticket=ticket, code=code_at(secret, NOW)), db)
        _session_of(outcome, user)
    assert ("two_step_failed", {"method": "totp"}) in await _audit(sf)


@pytest.mark.parametrize("code", [None, "", "   ", "12345", "abcdef"])
async def test_a_missing_or_malformed_code_is_a_wrong_code(sf, clock, code):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        refused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, code=code), db))
        assert refused.status_code == 401


async def test_a_code_cannot_be_used_from_two_browsers(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        first = (await _password_step(db, user, clock))["ticket"]
        second = (await _password_step(db, user, clock))["ticket"]
        code = code_at(secret, NOW)

        await two_step.verify(VerifyRequest(ticket=first, code=code), db)
        await db.commit()
        refused = await _refused(two_step.verify(VerifyRequest(ticket=second, code=code), db))

        assert refused.status_code == 401


async def test_five_wrong_codes_pause_the_second_step_for_five_minutes(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        for _ in range(4):
            refused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="000000"), db))
            assert refused.status_code == 401
        fifth = await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="000000"), db))
        assert fifth.status_code == 429
        assert fifth.detail == "Too many wrong tries. Try again in 5 minutes."
        assert fifth.headers["Retry-After"] == "300"

        # During the pause even the right code is refused, without being checked.
        clock.advance(MINUTE)
        paused = await _refused(two_step.verify(
            VerifyRequest(ticket=ticket, code=code_at(secret, clock.now)), db))
        assert paused.status_code == 429
        assert paused.detail == "Too many wrong tries. Try again in 4 minutes."

        # Afterwards a new sign-in works, with a code that was not burnt.
        clock.advance(4 * MINUTE)
        ticket = (await _password_step(db, user, clock))["ticket"]
        outcome = await two_step.verify(
            VerifyRequest(ticket=ticket, code=code_at(secret, clock.now)), db)
        _session_of(outcome, user)
    actions = [action for action, _ in await _audit(sf)]
    assert actions.count("two_step_failed") == 5 and actions.count("two_step_paused") == 1


async def test_the_pause_message_says_minute_for_one(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]
        for _ in range(5):
            await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="000000"), db))
        clock.advance(4 * MINUTE + 10 * timedelta(seconds=1))

        paused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="000000"), db))
        assert paused.detail == "Too many wrong tries. Try again in 1 minute."


async def test_a_right_code_forgets_earlier_wrong_ones(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        for round_ in range(3):
            ticket = (await _password_step(db, user, clock))["ticket"]
            for _ in range(4):
                await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="000000"), db))
            outcome = await two_step.verify(
                VerifyRequest(ticket=ticket, code=code_at(secret, clock.now)), db)
            await db.commit()
            _session_of(outcome, user)
            clock.advance(STEP)


@pytest.mark.parametrize("minutes, alive", [(4, True), (5, False)])
async def test_a_ticket_lasts_five_minutes(sf, clock, minutes, alive):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]
        clock.advance(minutes * MINUTE)
        call = two_step.verify(VerifyRequest(ticket=ticket, code=code_at(secret, clock.now)), db)

        if alive:
            _session_of(await call, user)
        else:
            assert (await _refused(call)).status_code == 410


@pytest.mark.parametrize("ticket", ["", "nonsense", "x" * 200])
async def test_an_unknown_ticket_is_gone(sf, clock, ticket):
    async with sf() as db:
        refused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, code="123456"), db))
        assert refused.status_code == 410


async def test_an_account_deactivated_in_between_cannot_finish(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        secret, _ = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]
        user.is_active = False
        await db.commit()

        refused = await _refused(two_step.verify(
            VerifyRequest(ticket=ticket, code=code_at(secret, NOW)), db))
        assert refused.status_code == 410


async def test_one_accounts_ticket_does_not_take_anothers_code(sf, clock):
    async with sf() as db:
        owner, other = await add_user(db), await add_user(db)
        await turn_on_totp(db, owner)
        other_secret, _ = await turn_on_totp(db, other)
        ticket = (await _password_step(db, owner, clock))["ticket"]

        refused = await _refused(two_step.verify(
            VerifyRequest(ticket=ticket, code=code_at(other_secret, NOW)), db))
        assert refused.status_code == 401


# ── the second step: a recovery code ────────────────────────────────────

async def test_a_recovery_code_signs_in_once_and_says_how_many_are_left(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        _, codes = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        outcome = await two_step.verify(VerifyRequest(ticket=ticket, recovery_code=codes[0]), db)
        await db.commit()
        _session_of(outcome, user)
        assert outcome["recovery_codes_left"] == 9

        ticket = (await _password_step(db, user, clock))["ticket"]
        refused = await _refused(two_step.verify(
            VerifyRequest(ticket=ticket, recovery_code=codes[0]), db))
        assert (refused.status_code, refused.detail) == (401, "That code is not right.")
    actions = [action for action, _ in await _audit(sf)]
    assert "two_step_recovery_code_used" in actions
    assert ("login_success", {"second_step": "recovery_code"}) in await _audit(sf)


async def test_wrong_recovery_codes_count_towards_the_pause(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        _, codes = await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        for _ in range(4):
            await _refused(two_step.verify(VerifyRequest(ticket=ticket, recovery_code="abcde-fghjk"), db))
        fifth = await _refused(two_step.verify(
            VerifyRequest(ticket=ticket, recovery_code="abcde-fghjk"), db))
        assert fifth.status_code == 429

        paused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, recovery_code=codes[0]), db))
        assert paused.status_code == 429
        assert await recovery_codes.remaining(db, user.id) == 10


async def test_a_recovery_code_works_for_an_account_with_passkeys_only(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        _, codes = await add_passkey(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        outcome = await two_step.verify(VerifyRequest(ticket=ticket, recovery_code=codes[3]), db)
        _session_of(outcome, user)


# ── the second step: a passkey ──────────────────────────────────────────

async def _passkey_answer(db, ticket: str, device) -> dict:
    options = (await two_step.passkey_options(TicketRequest(ticket=ticket), db))["options"]
    await db.commit()
    return device.get(options)


async def test_a_passkey_signs_in(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        device, _ = await add_passkey(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        answer = await _passkey_answer(db, ticket, device)
        outcome = await two_step.passkey_verify(PasskeyAnswer(ticket=ticket, credential=answer), db)
        await db.commit()

        _session_of(outcome, user)
    assert ("login_success", {"second_step": "passkey"}) in await _audit(sf)


async def test_a_passkey_answer_cannot_be_replayed(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        device, _ = await add_passkey(db, user)
        first = (await _password_step(db, user, clock))["ticket"]
        answer = await _passkey_answer(db, first, device)
        await two_step.passkey_verify(PasskeyAnswer(ticket=first, credential=answer), db)
        await db.commit()

        # The same answer, on a new ticket that was given its own challenge.
        second = (await _password_step(db, user, clock))["ticket"]
        await two_step.passkey_options(TicketRequest(ticket=second), db)
        await db.commit()
        refused = await _refused(two_step.passkey_verify(
            PasskeyAnswer(ticket=second, credential=answer), db))

        assert (refused.status_code, refused.detail) == (401, "That passkey was not accepted.")


async def test_a_challenge_is_spent_by_a_wrong_answer(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        device, _ = await add_passkey(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]
        options = (await two_step.passkey_options(TicketRequest(ticket=ticket), db))["options"]
        await db.commit()
        stranger = SoftwareAuthenticator()

        await _refused(two_step.passkey_verify(
            PasskeyAnswer(ticket=ticket, credential=stranger.get(options)), db))
        # The right device answering the same, now spent, challenge:
        refused = await _refused(two_step.passkey_verify(
            PasskeyAnswer(ticket=ticket, credential=device.get(options)), db))
        assert refused.status_code == 401

        # With a new challenge it works.
        answer = await _passkey_answer(db, ticket, device)
        _session_of(await two_step.passkey_verify(PasskeyAnswer(ticket=ticket, credential=answer), db), user)


async def test_an_answer_without_a_challenge_is_refused_and_not_counted(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        device, _ = await add_passkey(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        for _ in range(6):
            refused = await _refused(two_step.passkey_verify(
                PasskeyAnswer(ticket=ticket, credential=device.get({"challenge": "AAAA"})), db))
            assert refused.status_code == 401


async def test_wrong_passkey_answers_count_towards_the_pause(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        await add_passkey(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]
        stranger = SoftwareAuthenticator()

        statuses = []
        for _ in range(5):
            answer = await _passkey_answer(db, ticket, stranger)
            refused = await _refused(two_step.passkey_verify(
                PasskeyAnswer(ticket=ticket, credential=answer), db))
            statuses.append(refused.status_code)
        assert statuses == [401, 401, 401, 401, 429]

        paused = await _refused(two_step.passkey_options(TicketRequest(ticket=ticket), db))
        assert paused.status_code == 429


async def test_a_copied_passkey_is_refused_and_recorded(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        device, _ = await add_passkey(db, user)
        for _ in range(2):
            ticket = (await _password_step(db, user, clock))["ticket"]
            answer = await _passkey_answer(db, ticket, device)
            await two_step.passkey_verify(PasskeyAnswer(ticket=ticket, credential=answer), db)
            await db.commit()

        device.sign_count = 0  # a copy made before those two sign-ins
        ticket = (await _password_step(db, user, clock))["ticket"]
        answer = await _passkey_answer(db, ticket, device)
        refused = await _refused(two_step.passkey_verify(
            PasskeyAnswer(ticket=ticket, credential=answer), db))

        assert refused.status_code == 401
    assert "two_step_passkey_counter_went_back" in [action for action, _ in await _audit(sf)]


async def test_an_account_without_passkeys_is_offered_no_challenge(sf, clock):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]

        refused = await _refused(two_step.passkey_options(TicketRequest(ticket=ticket), db))
        assert refused.status_code == 400


@pytest.mark.parametrize("credential", [{}, {"rawId": "x"}, {"rawId": 5, "response": []}])
async def test_a_malformed_passkey_answer_is_a_wrong_answer(sf, clock, credential):
    async with sf() as db:
        user = await add_user(db)
        await add_passkey(db, user)
        ticket = (await _password_step(db, user, clock))["ticket"]
        await two_step.passkey_options(TicketRequest(ticket=ticket), db)
        await db.commit()

        refused = await _refused(two_step.passkey_verify(
            PasskeyAnswer(ticket=ticket, credential=credential), db))
        assert refused.status_code == 401


# ── the forced setup ────────────────────────────────────────────────────

async def _owing_setup(db, clock):
    user = await add_user(db)
    await status.set_required(db, True)
    outcome = await _password_step(db, user, clock)
    assert outcome["status"] == "two_step_setup_required"
    return user, outcome["ticket"]


async def test_setting_an_authenticator_up_ends_in_a_session(sf, clock):
    async with sf() as db:
        user, ticket = await _owing_setup(db, clock)

        setup = await two_step.setup_totp(TicketRequest(ticket=ticket), db)
        assert setup["secret"] in setup["otpauth_uri"]

        wrong = await _refused(two_step.setup_totp_confirm(
            TicketCodeRequest(ticket=ticket, code="000000"), db))
        assert (wrong.status_code, wrong.detail) == (400, "That code is not right.")

        confirmed = await two_step.setup_totp_confirm(
            TicketCodeRequest(ticket=ticket, code=code_at(setup["secret"], NOW)), db)
        await db.commit()
        assert len(confirmed["recovery_codes"]) == 10
        assert await status.methods(db, user.id) == ["totp"]

        outcome = await two_step.setup_finish(TicketRequest(ticket=ticket), db)
        await db.commit()
        _session_of(outcome, user)
        again = await _refused(two_step.setup_finish(TicketRequest(ticket=ticket), db))
        assert again.status_code == 410
    audit = await _audit(sf)
    assert ("two_step_method_added", {"method": "totp", "during": "sign_in"}) in audit
    assert ("login_success", {"second_step": "setup"}) in audit


async def test_setting_a_passkey_up_ends_in_a_session(sf, clock):
    async with sf() as db:
        user, ticket = await _owing_setup(db, clock)
        device = SoftwareAuthenticator()

        options = (await two_step.setup_passkey_options(TicketRequest(ticket=ticket), db))["options"]
        await db.commit()
        added = await two_step.setup_passkey(
            NamedPasskeyAnswer(ticket=ticket, credential=device.create(options), name="Work laptop"), db)
        await db.commit()

        assert len(added["recovery_codes"]) == 10
        assert (await status.summary(db, user.id))["passkeys"][0]["name"] == "Work laptop"
        _session_of(await two_step.setup_finish(TicketRequest(ticket=ticket), db), user)


async def test_a_bad_passkey_answer_during_setup_adds_nothing(sf, clock):
    async with sf() as db:
        user, ticket = await _owing_setup(db, clock)
        phishing = SoftwareAuthenticator(origin="https://megooci.evil.example")
        options = (await two_step.setup_passkey_options(TicketRequest(ticket=ticket), db))["options"]
        await db.commit()

        refused = await _refused(two_step.setup_passkey(
            NamedPasskeyAnswer(ticket=ticket, credential=phishing.create(options)), db))

        assert refused.status_code == 400
        assert await status.is_on(db, user.id) is False
        assert (await _refused(two_step.setup_finish(TicketRequest(ticket=ticket), db))).status_code == 410


async def test_there_is_no_session_before_a_method_is_set_up(sf, clock):
    async with sf() as db:
        _, ticket = await _owing_setup(db, clock)
        await two_step.setup_totp(TicketRequest(ticket=ticket), db)
        await db.commit()

        refused = await _refused(two_step.setup_finish(TicketRequest(ticket=ticket), db))
        assert refused.status_code == 410


async def test_a_setup_ticket_adds_one_method_then_only_finishes(sf, clock):
    async with sf() as db:
        user, ticket = await _owing_setup(db, clock)
        setup = await two_step.setup_totp(TicketRequest(ticket=ticket), db)
        await two_step.setup_totp_confirm(
            TicketCodeRequest(ticket=ticket, code=code_at(setup["secret"], NOW)), db)
        await db.commit()

        for call in (
            two_step.setup_totp(TicketRequest(ticket=ticket), db),
            two_step.setup_passkey_options(TicketRequest(ticket=ticket), db),
        ):
            assert (await _refused(call)).status_code == 410
        _session_of(await two_step.setup_finish(TicketRequest(ticket=ticket), db), user)


async def test_a_setup_ticket_is_worthless_once_the_account_has_a_method_from_elsewhere(sf, clock):
    """Someone who has only the password holds a setup ticket. The owner then
    sets two-step verification up from their own session. The ticket must not
    add a method, and must not become a session."""
    async with sf() as db:
        user, ticket = await _owing_setup(db, clock)
        await turn_on_totp(db, user)  # the owner, in Settings
        await db.commit()

        for call in (
            two_step.setup_totp(TicketRequest(ticket=ticket), db),
            two_step.setup_passkey_options(TicketRequest(ticket=ticket), db),
            two_step.setup_finish(TicketRequest(ticket=ticket), db),
        ):
            assert (await _refused(call)).status_code == 410
        assert await status.methods(db, user.id) == ["totp"]


async def test_a_reset_between_setup_and_finish_leaves_no_session(sf, clock):
    async with sf() as db:
        user, ticket = await _owing_setup(db, clock)
        setup = await two_step.setup_totp(TicketRequest(ticket=ticket), db)
        await two_step.setup_totp_confirm(
            TicketCodeRequest(ticket=ticket, code=code_at(setup["secret"], NOW)), db)
        await db.commit()
        await reset(db, user.id)
        await db.commit()

        refused = await _refused(two_step.setup_finish(TicketRequest(ticket=ticket), db))
        assert refused.status_code == 410


async def test_tickets_do_not_cross_between_the_second_step_and_setup(sf, clock):
    async with sf() as db:
        _, setup_ticket = await _owing_setup(db, clock)
        enrolled = await add_user(db)
        secret, _ = await turn_on_totp(db, enrolled)
        verify_ticket = (await _password_step(db, enrolled, clock))["ticket"]

        assert (await _refused(two_step.verify(
            VerifyRequest(ticket=setup_ticket, code="123456"), db))).status_code == 410
        for call in (
            two_step.setup_totp(TicketRequest(ticket=verify_ticket), db),
            two_step.setup_finish(TicketRequest(ticket=verify_ticket), db),
        ):
            assert (await _refused(call)).status_code == 410
        # The verify ticket itself is unharmed.
        _session_of(await two_step.verify(
            VerifyRequest(ticket=verify_ticket, code=code_at(secret, NOW)), db), enrolled)


async def test_a_setup_ticket_lasts_fifteen_minutes(sf, clock):
    async with sf() as db:
        _, ticket = await _owing_setup(db, clock)
        clock.advance(14 * MINUTE)
        await two_step.setup_totp(TicketRequest(ticket=ticket), db)
        clock.advance(MINUTE)

        assert (await _refused(two_step.setup_totp(TicketRequest(ticket=ticket), db))).status_code == 410


# ── sign-up and invitations ─────────────────────────────────────────────

@pytest.fixture
def signup_open(monkeypatch):
    monkeypatch.setattr(get_settings(), "MEGOOCI_SIGNUP_ENABLED", True)


async def test_signing_up_gives_a_session_when_it_is_not_required(sf, signup_open):
    async with sf() as db:
        await add_user(db)  # so the new account is not the first, which becomes the administrator
        await db.commit()
        outcome = await auth.signup(
            SignupRequest(email="new@example.com", name="New", password="long enough"), db)

        assert outcome["status"] == "signed_in" and "access_token" in outcome


async def test_signing_up_leads_to_setup_when_it_is_required(sf, signup_open):
    async with sf() as db:
        await add_user(db)
        await status.set_required(db, True)
        await db.commit()
        outcome = await auth.signup(
            SignupRequest(email="new@example.com", name="New", password="long enough"), db)

        assert outcome["status"] == "two_step_setup_required"
        assert "access_token" not in outcome and outcome["ticket"]


async def _invitation(db, token: str) -> Invite:
    role = Role(id=uuid.uuid4(), name="developer", permissions=["projects.read"])
    db.add(role)
    await db.flush()
    invite = Invite(
        id=uuid.uuid4(), email="invited@example.com", role_id=role.id,
        token_hash=hashlib.sha256(token.encode()).hexdigest(), status="pending",
        expires_at=NOW + timedelta(days=3650),
    )
    db.add(invite)
    await db.flush()
    return invite


@pytest.mark.parametrize("required, expected", [
    (False, "signed_in"), (True, "two_step_setup_required"),
])
async def test_accepting_an_invitation_follows_the_same_rule(sf, required, expected):
    async with sf() as db:
        # Held on to: the route compares the expiry with an aware "now", and
        # SQLite would hand a reloaded invitation back with a naive one.
        invite = await _invitation(db, "invite-token")
        await status.set_required(db, required)
        outcome = await invites.accept_invite(
            AcceptInviteRequest(token="invite-token", name="Invited", password="long enough"), db)

        assert outcome["status"] == expected
        assert ("access_token" in outcome) is (expected == "signed_in")
        assert invite.status == "accepted"


# ── renewing a session ──────────────────────────────────────────────────

async def test_a_session_renews_as_before(sf):
    async with sf() as db:
        user = await add_user(db)
        renewed = await auth.refresh_token(
            RefreshRequest(refresh_token=create_refresh_token({"sub": str(user.id)})), db)

        assert decode_token(renewed["access_token"])["sub"] == str(user.id)
        assert decode_token(renewed["refresh_token"])["type"] == "refresh"


async def test_renewal_is_refused_once_setup_is_owed(sf):
    async with sf() as db:
        owing, enrolled = await add_user(db), await add_user(db)
        await turn_on_totp(db, enrolled)
        await status.set_required(db, True)

        refused = await _refused(auth.refresh_token(
            RefreshRequest(refresh_token=create_refresh_token({"sub": str(owing.id)})), db))
        assert refused.status_code == 401 and "Two-step verification is required" in refused.detail

        renewed = await auth.refresh_token(
            RefreshRequest(refresh_token=create_refresh_token({"sub": str(enrolled.id)})), db)
        assert "access_token" in renewed


# ── what the app is told about the account ──────────────────────────────

async def test_me_says_whether_it_is_on_and_whether_setup_is_owed(sf):
    async with sf() as db:
        user = await add_user(db)
        me = await auth.get_me(user, db)
        assert (me["two_step_enabled"], me["two_step_setup_required"]) == (False, False)

        await status.set_required(db, True)
        me = await auth.get_me(user, db)
        assert (me["two_step_enabled"], me["two_step_setup_required"]) == (False, True)

        await turn_on_totp(db, user)
        me = await auth.get_me(user, db)
        assert (me["two_step_enabled"], me["two_step_setup_required"]) == (True, False)
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_api_sign_in.py
```

Expected: a collection error, `ImportError: cannot import name 'two_step' from 'app.api.v1'`.

- [ ] **Step 3: Write the request and response models**

Create `backend/app/schemas/two_step.py`:

```python
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

_TICKET = Field(max_length=200)
_CODE = Field(default=None, max_length=64)


class SignInResponse(BaseModel):
    """The answer to a correct password. ``signed_in`` carries the session;
    the other two carry a ticket and never a token."""

    status: Literal["signed_in", "two_step_required", "two_step_setup_required"]
    access_token: str | None = None
    refresh_token: str | None = None
    token_type: str | None = None
    ticket: str | None = None
    methods: list[str] | None = None
    passkey_origin: str | None = None
    # Set when a recovery code was used to sign in.
    recovery_codes_left: int | None = None


class TicketRequest(BaseModel):
    ticket: str = _TICKET


class VerifyRequest(BaseModel):
    ticket: str = _TICKET
    code: str | None = _CODE
    recovery_code: str | None = _CODE


class TicketCodeRequest(BaseModel):
    ticket: str = _TICKET
    code: str = Field(max_length=64)


class PasskeyAnswer(BaseModel):
    ticket: str = _TICKET
    credential: dict[str, Any]


class NamedPasskeyAnswer(PasskeyAnswer):
    name: str | None = Field(default=None, max_length=300)


class PasskeyOptions(BaseModel):
    options: dict[str, Any]
    # Only when adding a passkey from Settings: hand it back with the answer.
    ticket: str | None = None


class TotpSetup(BaseModel):
    secret: str
    otpauth_uri: str


class RecoveryCodes(BaseModel):
    """Ten new codes, or None when the account already had its codes."""

    recovery_codes: list[str] | None = None


class CodeRequest(BaseModel):
    code: str = Field(max_length=64)


class PasswordRequest(BaseModel):
    password: str = Field(max_length=1024)


class RenameRequest(BaseModel):
    name: str = Field(max_length=300)


class PasskeyInfo(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime | None = None
    last_used_at: datetime | None = None


class PasskeyAdded(BaseModel):
    passkey: PasskeyInfo
    recovery_codes: list[str] | None = None


class TotpInfo(BaseModel):
    confirmed_at: datetime | None = None
    last_used_at: datetime | None = None


class RecoveryCodesInfo(BaseModel):
    left: int
    generated_at: datetime | None = None


class TwoStepSummary(BaseModel):
    enabled: bool
    required: bool
    totp: TotpInfo | None = None
    passkeys: list[PasskeyInfo] = []
    recovery_codes: RecoveryCodesInfo | None = None
    passkey_origin: str


class SecuritySettings(BaseModel):
    require_two_step: bool
    users_total: int
    users_with_two_step: int


class SecuritySettingsUpdate(BaseModel):
    require_two_step: bool
```

- [ ] **Step 4: Write the routes**

Create `backend/app/api/v1/two_step.py`:

```python
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
```

- [ ] **Step 5: Make login, sign-up, invitations and renewal use the decision**

**`backend/app/schemas/auth.py`**. Replace:

````python
    role: str | None = None
    permissions: list[str] = []
````

with:

````python
    role: str | None = None
    permissions: list[str] = []
    two_step_enabled: bool = False
    # The server requires two-step verification and this account has none.
    two_step_setup_required: bool = False
````

**`backend/app/api/v1/auth.py`** — edit 1 of 9. Replace:

````python
    create_access_token,
    create_refresh_token,
    create_reset_token,
````

with:

````python
    create_reset_token,
````

**`backend/app/api/v1/auth.py`** — edit 2 of 9. Replace:

````python
    UserResponse,
)

logger = logging.getLogger(__name__)
````

with:

````python
    UserResponse,
)
from app.schemas.two_step import SignInResponse
from app.services.two_step import clock, sign_in
from app.services.two_step import status as two_step_status

logger = logging.getLogger(__name__)
````

**`backend/app/api/v1/auth.py`** — edit 3 of 9. Replace:

````python
@router.post("/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
````

with:

````python
@router.post(
    "/signup",
    response_model=SignInResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
````

**`backend/app/api/v1/auth.py`** — edit 4 of 9. Replace:

````python
    if is_first_user:
        settings.MEGOOCI_SIGNUP_ENABLED = False

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }
````

with:

````python
    if is_first_user:
        settings.MEGOOCI_SIGNUP_ENABLED = False

    # On a server that requires two-step verification, a new account sets
    # it up before it gets a session.
    return await sign_in.decide(db, user, clock.now())
````

**`backend/app/api/v1/auth.py`** — edit 5 of 9. Replace:

````python
@router.post("/login", response_model=TokenResponse)
````

with:

````python
@router.post("/login", response_model=SignInResponse, response_model_exclude_none=True)
````

**`backend/app/api/v1/auth.py`** — edit 6 of 9. Replace:

````python
    await audit_record(action="login_success", actor_id=user.id)

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }
````

with:

````python
    # The password is right. Whether that is enough depends on the account:
    # with two-step verification the session comes after the second step,
    # and that is when the sign-in is recorded.
    outcome = await sign_in.decide(db, user, clock.now())
    if outcome["status"] == sign_in.SIGNED_IN:
        await audit_record(action="login_success", actor_id=user.id)
    return outcome
````

**`backend/app/api/v1/auth.py`** — edit 7 of 9. Replace:

````python
            detail="User not found or inactive",
        )

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }
````

with:

````python
            detail="User not found or inactive",
        )

    if not await sign_in.may_renew(db, user.id):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Two-step verification is required. Sign in again to set it up.",
        )

    return sign_in.session(user)
````

**`backend/app/api/v1/auth.py`** — edit 8 of 9. Replace:

````python
@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
) -> dict:
    from app.core.deps import _collect_permissions, get_user_primary_role_name

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
        "auth_provider": current_user.auth_provider,
        "created_at": current_user.created_at,
        "role": get_user_primary_role_name(current_user),
        "permissions": sorted(_collect_permissions(current_user)),
    }
````

with:

````python
async def _two_step_fields(db: AsyncSession, user_id) -> dict:
    """Whether the account has two-step verification on, and whether it still
    owes the server a setup (the app shows a banner for that)."""
    enabled = await two_step_status.is_on(db, user_id)
    return {
        "two_step_enabled": enabled,
        "two_step_setup_required": not enabled and await two_step_status.is_required(db),
    }


@router.get("/me", response_model=UserResponse)
async def get_me(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.core.deps import _collect_permissions, get_user_primary_role_name

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
        "auth_provider": current_user.auth_provider,
        "created_at": current_user.created_at,
        "role": get_user_primary_role_name(current_user),
        "permissions": sorted(_collect_permissions(current_user)),
        **await _two_step_fields(db, current_user.id),
    }
````

**`backend/app/api/v1/auth.py`** — edit 9 of 9. Replace:

````python
    current_user.active_token_scopes = token_scopes

    from app.core.deps import _collect_permissions, get_user_primary_role_name

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
        "auth_provider": current_user.auth_provider,
        "created_at": current_user.created_at,
        "role": get_user_primary_role_name(current_user),
        "permissions": sorted(_collect_permissions(current_user)),
    }
````

with:

````python
    current_user.active_token_scopes = token_scopes

    from app.core.deps import _collect_permissions, get_user_primary_role_name

    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
        "auth_provider": current_user.auth_provider,
        "created_at": current_user.created_at,
        "role": get_user_primary_role_name(current_user),
        "permissions": sorted(_collect_permissions(current_user)),
        **await _two_step_fields(db, current_user.id),
    }
````

**`backend/app/api/v1/invites.py`** — edit 1 of 4. Replace:

````python
from app.schemas.auth import TokenResponse
````

with:

````python
from app.schemas.two_step import SignInResponse
````

**`backend/app/api/v1/invites.py`** — edit 2 of 4. Replace:

````python
from app.models.user import User
````

with:

````python
from app.models.user import User
from app.services.two_step import clock, sign_in
````

**`backend/app/api/v1/invites.py`** — edit 3 of 4. Replace:

````python
@router.post("/accept", response_model=TokenResponse)
async def accept_invite(
    body: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    from app.core.security import create_access_token, create_refresh_token
````

with:

````python
@router.post("/accept", response_model=SignInResponse, response_model_exclude_none=True)
async def accept_invite(
    body: AcceptInviteRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
````

**`backend/app/api/v1/invites.py`** — edit 4 of 4. Replace:

````python
    await db.flush()

    token_data = {"sub": str(user.id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }
````

with:

````python
    await db.flush()

    # On a server that requires two-step verification, a new account sets
    # it up before it gets a session.
    return await sign_in.decide(db, user, clock.now())
````

**`backend/app/api/v1/router.py`** — edit 1 of 2. Replace:

````python
    system,
    user_notifications,
````

with:

````python
    system,
    two_step,
    user_notifications,
````

**`backend/app/api/v1/router.py`** — edit 2 of 2. Replace:

````python
api_v1_router.include_router(auth.router, prefix="/auth", tags=["auth"])
````

with:

````python
api_v1_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_v1_router.include_router(two_step.router, prefix="/auth/two-step", tags=["auth"])
````

- [ ] **Step 6: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_api_sign_in.py tests/test_password_reset_token.py tests/test_signup_status.py
```

Expected: `69 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/two_step.py backend/app/schemas/auth.py backend/app/api/v1/two_step.py backend/app/api/v1/auth.py backend/app/api/v1/invites.py backend/app/api/v1/router.py backend/tests/test_two_step_api_sign_in.py
git commit -m "feat(two-step): the second step and the forced setup when signing in

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: One's own account — the routes

**Files:**
- Create: `backend/app/api/v1/two_step_account.py`
- Modify: `backend/app/core/deps.py`, `backend/app/api/v1/router.py`
- Test: `backend/tests/test_two_step_api_account.py` (new)

**Interfaces:**
- Consumes: Tasks 3 to 8; `get_current_active_user`, `verify_password`.
- Produces, in `app.core.deps`: every user returned by `authenticate_pat` has `authenticated_with_token = True`; every user returned by the JWT path of `get_current_user` has it `False`.
- Produces, in `app.api.v1.two_step_account`: `router`, `session_user(current_user) -> User` (403 for an API token), and the route functions `get_two_step(user, db)`, `begin_totp(user, db)`, `confirm_totp(body, user, db)`, `remove_totp(body, user, db)`, `passkey_registration_options(user, db)`, `add_passkey(body, user, db)`, `rename_passkey(passkey_id, body, user, db)`, `remove_passkey(passkey_id, body, user, db)`, `regenerate_recovery_codes(body, user, db)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_api_account.py`:

```python
"""Managing one's own two-step verification: the routes behind the Settings tab."""
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import two_step, two_step_account as account
from app.core.deps import authenticate_pat, get_current_user
from app.core.security import create_access_token
from app.models.audit import AuditLogEntry
from app.schemas.two_step import (
    CodeRequest,
    NamedPasskeyAnswer,
    PasswordRequest,
    RenameRequest,
    TwoStepSummary,
    VerifyRequest,
)
from app.services.two_step import passkeys, recovery_codes, sign_in, status
from tests._mcp import seed_member, seed_token
from tests._passkey import SoftwareAuthenticator
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, PASSWORD, add_passkey, add_user, code_at, turn_on_totp

RIGHT = PasswordRequest(password=PASSWORD)
WRONG = PasswordRequest(password="not it")


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr("app.core.audit.async_session", factory)
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr("app.services.two_step.clock.now", lambda: NOW)


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value


async def _audited(db) -> list[tuple[str, dict | None]]:
    rows = (await db.execute(sa.select(AuditLogEntry))).scalars().all()
    return [(row.action, row.metadata_json) for row in rows]


# ── who may use it ──────────────────────────────────────────────────────

def test_every_route_needs_a_browser_session():
    assert len(account.router.routes) == 9
    for route in account.router.routes:
        calls = [dependency.call for dependency in route.dependant.dependencies]
        assert account.session_user in calls, route.path


def test_they_are_mounted_under_auth_two_step():
    from app.api.v1.router import api_v1_router

    paths = {
        (route.path, method)
        for route in api_v1_router.routes
        for method in getattr(route, "methods", None) or ()
    }
    assert {
        ("/auth/two-step", "GET"), ("/auth/two-step/totp", "POST"),
        ("/auth/two-step/passkeys/{passkey_id}", "PATCH"),
        ("/auth/two-step/recovery-codes", "POST"),
    } <= paths


async def test_an_api_token_is_refused_and_a_session_is_not(sf):
    async with sf() as db:
        user_id = await seed_member(db, ["admin"])
        full_access = await seed_token(db, user_id)
        scoped = await seed_token(db, user_id, scopes=["admin"])
        await db.commit()

        for token in (full_access, scoped):
            by_token = await authenticate_pat(db, token)
            refused = await _refused(account.session_user(by_token))
            assert refused.status_code == 403 and "API token" in refused.detail
            # ...and the same through the dependency every route starts from.
            assert (await get_current_user(token, db)).authenticated_with_token is True

        by_session = await get_current_user(create_access_token({"sub": str(user_id)}), db)
        assert by_session.authenticated_with_token is False
        assert await account.session_user(by_session) is by_session


# ── the summary ─────────────────────────────────────────────────────────

async def test_the_summary_of_an_account_with_nothing(sf):
    async with sf() as db:
        user = await add_user(db)
        summary = TwoStepSummary(**await account.get_two_step(user, db))

        assert (summary.enabled, summary.required) == (False, False)
        assert summary.totp is None and summary.passkeys == [] and summary.recovery_codes is None
        assert summary.passkey_origin == "http://localhost:3000"


# ── authenticator app ───────────────────────────────────────────────────

async def test_setting_an_authenticator_up_turns_it_on_and_gives_codes(sf):
    async with sf() as db:
        user = await add_user(db)
        setup = await account.begin_totp(user, db)

        wrong = await _refused(account.confirm_totp(CodeRequest(code="000000"), user, db))
        assert (wrong.status_code, wrong.detail) == (400, "That code is not right.")
        assert (await account.get_two_step(user, db))["enabled"] is False

        confirmed = await account.confirm_totp(CodeRequest(code=code_at(setup["secret"], NOW)), user, db)
        summary = await account.get_two_step(user, db)

        assert len(confirmed["recovery_codes"]) == 10
        assert summary["enabled"] is True and summary["totp"]["confirmed_at"] == NOW
        assert summary["recovery_codes"]["left"] == 10
        assert ("two_step_method_added", {"method": "totp"}) in await _audited(db)


async def test_wrong_codes_while_setting_up_never_pause_the_account(sf):
    async with sf() as db:
        user = await add_user(db)
        setup = await account.begin_totp(user, db)

        for _ in range(8):
            await _refused(account.confirm_totp(CodeRequest(code="000000"), user, db))
        await account.confirm_totp(CodeRequest(code=code_at(setup["secret"], NOW)), user, db)

        assert (await account.get_two_step(user, db))["enabled"] is True


async def test_a_second_authenticator_is_refused(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)

        refused = await _refused(account.begin_totp(user, db))
        assert refused.status_code == 409 and "already set up" in refused.detail


async def test_adding_a_second_method_gives_no_new_codes(sf):
    async with sf() as db:
        user = await add_user(db)
        await add_passkey(db, user)
        setup = await account.begin_totp(user, db)
        confirmed = await account.confirm_totp(CodeRequest(code=code_at(setup["secret"], NOW)), user, db)

        assert confirmed["recovery_codes"] is None
        assert await recovery_codes.remaining(db, user.id) == 10


async def test_removing_the_authenticator_needs_the_password(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)

        refused = await _refused(account.remove_totp(WRONG, user, db))
        assert (refused.status_code, refused.detail) == (400, "Wrong password.")
        assert (await account.get_two_step(user, db))["enabled"] is True

        summary = await account.remove_totp(RIGHT, user, db)
        assert summary["enabled"] is False and summary["totp"] is None
        # The last method went, and the recovery codes with it.
        assert summary["recovery_codes"] is None
        assert ("two_step_method_removed", {"method": "totp"}) in await _audited(db)


async def test_removing_one_of_two_methods_keeps_it_on(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await add_passkey(db, user)

        summary = await account.remove_totp(RIGHT, user, db)
        assert summary["enabled"] is True and summary["recovery_codes"]["left"] == 10


async def test_removing_an_authenticator_that_is_not_there(sf):
    async with sf() as db:
        user = await add_user(db)
        assert (await _refused(account.remove_totp(RIGHT, user, db))).status_code == 404


async def test_an_account_without_a_password_cannot_confirm_with_one(sf):
    async with sf() as db:
        user = await add_user(db, password=None)
        await turn_on_totp(db, user)

        refused = await _refused(account.remove_totp(PasswordRequest(password=""), user, db))
        assert refused.status_code == 400


# ── passkeys ────────────────────────────────────────────────────────────

async def _add_through_the_routes(db, user, device=None, name="Laptop") -> dict:
    device = device or SoftwareAuthenticator()
    offered = await account.passkey_registration_options(user, db)
    return await account.add_passkey(
        NamedPasskeyAnswer(ticket=offered["ticket"], credential=device.create(offered["options"]),
                           name=name), user, db)


async def test_adding_a_passkey_turns_it_on_and_gives_codes(sf):
    async with sf() as db:
        user = await add_user(db)
        added = await _add_through_the_routes(db, user, name="Work laptop")
        summary = await account.get_two_step(user, db)

        assert added["passkey"]["name"] == "Work laptop" and len(added["recovery_codes"]) == 10
        assert summary["enabled"] is True
        assert [key["name"] for key in summary["passkeys"]] == ["Work laptop"]
        assert ("two_step_method_added", {"method": "passkey"}) in await _audited(db)

        second = await _add_through_the_routes(db, user, name="YubiKey")
        assert second["recovery_codes"] is None


async def test_a_passkey_answer_needs_the_ticket_it_was_offered_with(sf):
    async with sf() as db:
        user, other = await add_user(db), await add_user(db)
        device = SoftwareAuthenticator()
        offered = await account.passkey_registration_options(user, db)
        answer = device.create(offered["options"])

        # No ticket, a made-up one, and one that belongs to someone else.
        for ticket in ("", "nonsense"):
            refused = await _refused(account.add_passkey(
                NamedPasskeyAnswer(ticket=ticket, credential=answer), user, db))
            assert refused.status_code == 400
        refused = await _refused(account.add_passkey(
            NamedPasskeyAnswer(ticket=offered["ticket"], credential=answer), other, db))
        assert refused.status_code == 400
        assert await passkeys.count(db, other.id) == 0

        added = await account.add_passkey(
            NamedPasskeyAnswer(ticket=offered["ticket"], credential=answer), user, db)
        assert added["passkey"]["id"]


async def test_a_passkey_answer_is_accepted_once(sf):
    async with sf() as db:
        user = await add_user(db)
        device = SoftwareAuthenticator()
        offered = await account.passkey_registration_options(user, db)
        body = NamedPasskeyAnswer(ticket=offered["ticket"], credential=device.create(offered["options"]))
        await account.add_passkey(body, user, db)

        assert (await _refused(account.add_passkey(body, user, db))).status_code == 400
        assert await passkeys.count(db, user.id) == 1


async def test_a_sign_in_ticket_cannot_add_a_passkey(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        sign_in_ticket = (await sign_in.decide(db, user, NOW))["ticket"]
        device = SoftwareAuthenticator()
        offered = await account.passkey_registration_options(user, db)

        refused = await _refused(account.add_passkey(
            NamedPasskeyAnswer(ticket=sign_in_ticket, credential=device.create(offered["options"])),
            user, db))
        assert refused.status_code == 400


async def test_a_bad_answer_adds_nothing_and_spends_the_ticket(sf):
    async with sf() as db:
        user = await add_user(db)
        phishing = SoftwareAuthenticator(origin="https://megooci.evil.example")
        honest = SoftwareAuthenticator()
        offered = await account.passkey_registration_options(user, db)

        refused = await _refused(account.add_passkey(
            NamedPasskeyAnswer(ticket=offered["ticket"], credential=phishing.create(offered["options"])),
            user, db))
        assert (refused.status_code, refused.detail) == (400, "That passkey was not accepted.")

        again = await _refused(account.add_passkey(
            NamedPasskeyAnswer(ticket=offered["ticket"], credential=honest.create(offered["options"])),
            user, db))
        assert again.status_code == 400
        assert (await account.get_two_step(user, db))["enabled"] is False


async def test_the_same_passkey_twice_is_a_conflict(sf):
    async with sf() as db:
        user = await add_user(db)
        device = SoftwareAuthenticator()
        await _add_through_the_routes(db, user, device)

        refused = await _refused(_add_through_the_routes(db, user, device))
        assert (refused.status_code, refused.detail) == (409, "This passkey is already registered.")


async def test_the_eleventh_passkey_is_refused(sf):
    async with sf() as db:
        user = await add_user(db)
        for _ in range(10):
            await add_passkey(db, user)

        refused = await _refused(account.passkey_registration_options(user, db))
        assert (refused.status_code, refused.detail) == (409, "An account can have 10 passkeys at most.")


async def test_a_passkey_is_renamed(sf):
    async with sf() as db:
        user, other = await add_user(db), await add_user(db)
        passkey_id = (await _add_through_the_routes(db, user))["passkey"]["id"]

        renamed = await account.rename_passkey(passkey_id, RenameRequest(name="  Desk  key "), user, db)
        assert renamed["name"] == "Desk key"
        for owner, target in ((other, passkey_id), (user, uuid.uuid4())):
            refused = await _refused(account.rename_passkey(target, RenameRequest(name="x"), owner, db))
            assert refused.status_code == 404


async def test_removing_a_passkey_needs_the_password_and_the_owner(sf):
    async with sf() as db:
        user, other = await add_user(db), await add_user(db)
        passkey_id = (await _add_through_the_routes(db, user))["passkey"]["id"]

        assert (await _refused(account.remove_passkey(passkey_id, WRONG, user, db))).status_code == 400
        assert (await _refused(account.remove_passkey(passkey_id, RIGHT, other, db))).status_code == 404
        assert await passkeys.count(db, user.id) == 1

        summary = await account.remove_passkey(passkey_id, RIGHT, user, db)
        assert summary["enabled"] is False and summary["passkeys"] == []
        assert summary["recovery_codes"] is None
        assert ("two_step_method_removed", {"method": "passkey"}) in await _audited(db)


# ── when it is required ─────────────────────────────────────────────────

async def test_the_last_method_cannot_be_removed_while_it_is_required(sf):
    async with sf() as db:
        with_app, with_key = await add_user(db), await add_user(db)
        await turn_on_totp(db, with_app)
        key_id = (await _add_through_the_routes(db, with_key))["passkey"]["id"]
        await status.set_required(db, True)

        for call in (
            account.remove_totp(RIGHT, with_app, db),
            account.remove_passkey(key_id, RIGHT, with_key, db),
        ):
            refused = await _refused(call)
            assert (refused.status_code, refused.detail) == (
                409, "Two-step verification is required on this server.")
        assert await status.is_on(db, with_app.id) and await status.is_on(db, with_key.id)


async def test_one_of_two_methods_can_be_removed_while_it_is_required(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        first = (await _add_through_the_routes(db, user))["passkey"]["id"]
        second = (await _add_through_the_routes(db, user))["passkey"]["id"]
        await status.set_required(db, True)

        await account.remove_totp(RIGHT, user, db)
        await account.remove_passkey(first, RIGHT, user, db)
        # Now the second passkey is the last method.
        assert (await _refused(account.remove_passkey(second, RIGHT, user, db))).status_code == 409
        assert (await account.get_two_step(user, db))["required"] is True


# ── recovery codes ──────────────────────────────────────────────────────

async def test_new_recovery_codes_need_the_password_and_cancel_the_old(sf):
    async with sf() as db:
        user = await add_user(db)
        secret, old = await turn_on_totp(db, user)

        assert (await _refused(account.regenerate_recovery_codes(WRONG, user, db))).status_code == 400
        assert await recovery_codes.use(db, user.id, old[0], NOW) is True

        new = (await account.regenerate_recovery_codes(RIGHT, user, db))["recovery_codes"]
        await db.commit()

        assert len(new) == 10 and not set(new) & set(old)
        assert (await account.get_two_step(user, db))["recovery_codes"]["left"] == 10
        assert ("two_step_recovery_codes_generated", None) in await _audited(db)

        # An old code no longer signs in; a new one does.
        ticket = (await sign_in.decide(db, user, NOW))["ticket"]
        await db.commit()
        refused = await _refused(two_step.verify(VerifyRequest(ticket=ticket, recovery_code=old[1]), db))
        assert refused.status_code == 401
        outcome = await two_step.verify(VerifyRequest(ticket=ticket, recovery_code=new[0]), db)
        assert outcome["status"] == "signed_in" and outcome["recovery_codes_left"] == 9


async def test_recovery_codes_cannot_be_made_while_it_is_off(sf):
    async with sf() as db:
        user = await add_user(db)
        refused = await _refused(account.regenerate_recovery_codes(RIGHT, user, db))

        assert (refused.status_code, refused.detail) == (409, "Turn two-step verification on first.")
        assert await recovery_codes.remaining(db, user.id) == 0


# ── nothing secret is ever returned ─────────────────────────────────────

async def test_the_summary_never_carries_a_secret(sf):
    async with sf() as db:
        user = await add_user(db)
        secret, codes = await turn_on_totp(db, user)
        device, _ = await add_passkey(db, user)
        text = TwoStepSummary(**await account.get_two_step(user, db)).model_dump_json()

        assert secret not in text
        assert not any(code in text for code in codes)
        assert set(TwoStepSummary.model_fields) == {
            "enabled", "required", "totp", "passkeys", "recovery_codes", "passkey_origin"}
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_api_account.py
```

Expected: a collection error, `ImportError: cannot import name 'two_step_account' from 'app.api.v1'`.

- [ ] **Step 3: Write the routes**

Create `backend/app/api/v1/two_step_account.py`:

```python
"""Two-step verification for one's own account: the Settings tab.

Every route needs a browser session. An API token cannot add or remove a
method, whatever its scope: a leaked token must not be able to weaken, or
take over, the way its owner signs in.
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
    CodeRequest,
    NamedPasskeyAnswer,
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
    body: CodeRequest,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
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
    body: NamedPasskeyAnswer,
    user: User = Depends(session_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
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
```

- [ ] **Step 4: Tell a token from a session, and mount the routes**

**`backend/app/core/deps.py`** — edit 1 of 2. Replace:

````python
    user.active_token_scopes = api_token.scopes
    return user
````

with:

````python
    user.active_token_scopes = api_token.scopes
    # A Full-access token has no scopes either, so the scope alone does not
    # tell a token from a browser session.
    user.authenticated_with_token = True
    return user
````

**`backend/app/core/deps.py`** — edit 2 of 2. Replace:

````python
    user.active_token_scopes = None
    return user
````

with:

````python
    user.active_token_scopes = None
    user.authenticated_with_token = False
    return user
````

**`backend/app/api/v1/router.py`** — edit 1 of 2. Replace:

````python
    two_step,
````

with:

````python
    two_step,
    two_step_account,
````

**`backend/app/api/v1/router.py`** — edit 2 of 2. Replace:

````python
api_v1_router.include_router(two_step.router, prefix="/auth/two-step", tags=["auth"])
````

with:

````python
api_v1_router.include_router(two_step.router, prefix="/auth/two-step", tags=["auth"])
api_v1_router.include_router(
    two_step_account.router, prefix="/auth/two-step", tags=["auth"]
)
````

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_api_account.py tests/test_mcp_auth.py
```

Expected: `35 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/two_step_account.py backend/app/core/deps.py backend/app/api/v1/router.py backend/tests/test_two_step_api_account.py
git commit -m "feat(two-step): manage one's own authenticator, passkeys and recovery codes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: Administrators — the requirement, the users list, the reset

**Files:**
- Create: `backend/app/reset_two_step.py`
- Modify: `backend/app/api/v1/system.py`, `backend/app/api/v1/users.py`, `backend/app/schemas/users.py`
- Test: `backend/tests/test_two_step_admin.py` (new), `backend/tests/test_two_step_http.py` (new)

**Interfaces:**
- Consumes: `status`, `reset.reset` (Task 7); `SecuritySettings`, `SecuritySettingsUpdate` (Task 8); `require_global_admin` from `app.api.v1.backups`; `require_permission("users.manage")`.
- Produces: `GET /system/security` and `PUT /system/security` (`get_security(db, _admin)`, `set_security(body, db, admin)`), returning `require_two_step`, `users_total`, `users_with_two_step`; `two_step_enabled` on every user the users routes return; `POST /users/{user_id}/two-step/reset` (`reset_user_two_step(user_id, db, current_user)`, 204, refused with 400 for one's own account).
- Produces: `app.reset_two_step.reset_by_email(session_factory, email: str) -> tuple[bool, str]` and `main(argv: list[str] | None = None) -> int` (0 done, 1 no such account, 2 wrong usage).
- The second test file drives the routes of Tasks 8 to 10 over real HTTP requests, mounted as `router.py` mounts them.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_admin.py`:

```python
"""What administrators get: the requirement, the users list, and the reset."""
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app import reset_two_step as command
from app.api.v1 import system, users
from app.api.v1.backups import require_global_admin
from app.models.audit import AuditLogEntry
from app.models.user import User
from app.schemas.two_step import SecuritySettings, SecuritySettingsUpdate
from app.schemas.users import UserDetailResponse
from app.services.two_step import recovery_codes, sign_in, status
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, add_passkey, add_user, turn_on_totp

ON = SecuritySettingsUpdate(require_two_step=True)
OFF = SecuritySettingsUpdate(require_two_step=False)


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr("app.core.audit.async_session", factory)
    yield factory
    await engine.dispose()


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value


async def _audited(db) -> list[tuple[str, uuid.UUID | None, uuid.UUID | None, dict | None]]:
    rows = (await db.execute(sa.select(AuditLogEntry))).scalars().all()
    return [(row.action, row.actor_id, row.target_id, row.metadata_json) for row in rows]


def _route(router, path: str, method: str):
    return next(r for r in router.routes if r.path == path and method in r.methods)


# ── the requirement ─────────────────────────────────────────────────────

def test_the_switch_is_for_administrators_of_the_whole_server():
    for method in ("GET", "PUT"):
        calls = [d.call for d in _route(system.router, "/security", method).dependant.dependencies]
        assert require_global_admin in calls, method


async def test_it_starts_off_and_counts_who_has_it(sf):
    async with sf() as db:
        admin, member = await add_user(db, is_admin=True), await add_user(db)
        await add_user(db, is_active=False)
        await turn_on_totp(db, member)

        settings = SecuritySettings(**await system.get_security(db, admin))
        assert settings.model_dump() == {
            "require_two_step": False, "users_total": 2, "users_with_two_step": 1}


async def test_an_administrator_without_it_cannot_require_it(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)

        refused = await _refused(system.set_security(ON, db, admin))

        assert (refused.status_code, refused.detail) == (
            409, "Set up two-step verification on your own account first.")
        assert await status.is_required(db) is False
        assert await _audited(db) == []


@pytest.mark.parametrize("give_method", [turn_on_totp, add_passkey])
async def test_an_administrator_with_it_can_require_it_and_stop(sf, give_method):
    async with sf() as db:
        admin, member = await add_user(db, is_admin=True), await add_user(db)
        await give_method(db, admin)

        turned_on = await system.set_security(ON, db, admin)
        assert turned_on["require_two_step"] is True
        assert (await sign_in.decide(db, member, NOW))["status"] == "two_step_setup_required"
        assert await sign_in.may_renew(db, member.id) is False

        turned_off = await system.set_security(OFF, db, admin)
        assert turned_off["require_two_step"] is False
        assert (await sign_in.decide(db, member, NOW))["status"] == "signed_in"
        assert [(action, actor, meta) for action, actor, _, meta in await _audited(db)] == [
            ("two_step_requirement_changed", admin.id, {"required": True}),
            ("two_step_requirement_changed", admin.id, {"required": False}),
        ]


async def test_saving_the_same_value_records_nothing(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await turn_on_totp(db, admin)
        await system.set_security(ON, db, admin)
        await system.set_security(ON, db, admin)
        await system.set_security(OFF, db, admin)
        await system.set_security(OFF, db, admin)

        assert len(await _audited(db)) == 2


async def test_turning_it_off_needs_no_method(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await status.set_required(db, True)  # as left by another administrator

        assert (await system.set_security(OFF, db, admin))["require_two_step"] is False


# ── the users list ──────────────────────────────────────────────────────

async def test_each_user_is_listed_with_whether_it_is_on(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        with_app, with_key, without = await add_user(db), await add_user(db), await add_user(db)
        await turn_on_totp(db, with_app)
        await add_passkey(db, with_key)
        await db.commit()

        listed = {row["id"]: UserDetailResponse(**row).two_step_enabled
                  for row in await users.list_users(0, 100, db, admin)}

        assert listed == {admin.id: False, with_app.id: True, with_key.id: True, without.id: False}
        assert (await users.get_user(with_app.id, db, admin))["two_step_enabled"] is True
        assert (await users.get_user(without.id, db, admin))["two_step_enabled"] is False


# ── the reset ───────────────────────────────────────────────────────────

def test_the_reset_needs_the_same_permission_as_deleting_a_user():
    def checks(path: str, method: str) -> list[str]:
        route = _route(users.router, path, method)
        return [d.call.__qualname__ for d in route.dependant.dependencies]

    assert checks("/{user_id}/two-step/reset", "POST") == checks("/{user_id}", "DELETE")
    assert "require_permission" in checks("/{user_id}/two-step/reset", "POST")[-1]


async def test_a_reset_clears_the_user_and_is_recorded(sf):
    async with sf() as db:
        admin, member, other = await add_user(db, is_admin=True), await add_user(db), await add_user(db)
        _, codes = await turn_on_totp(db, member)
        await add_passkey(db, member)
        await turn_on_totp(db, other)

        assert await users.reset_user_two_step(member.id, db, admin) is None

        assert await status.is_on(db, member.id) is False
        assert await recovery_codes.use(db, member.id, codes[0], NOW) is False
        assert await status.is_on(db, other.id) is True
        assert await _audited(db) == [
            ("two_step_reset_by_admin", admin.id, member.id, {"was_on": True})]
        # With a password alone they are in again...
        assert (await sign_in.decide(db, member, NOW))["status"] == "signed_in"
        # ...or sent to set it up, where it is required.
        await status.set_required(db, True)
        assert (await sign_in.decide(db, member, NOW))["status"] == "two_step_setup_required"


async def test_resetting_a_user_who_has_nothing_is_harmless_and_still_recorded(sf):
    async with sf() as db:
        admin, member = await add_user(db, is_admin=True), await add_user(db)
        await users.reset_user_two_step(member.id, db, admin)

        assert await _audited(db) == [
            ("two_step_reset_by_admin", admin.id, member.id, {"was_on": False})]


async def test_an_administrator_cannot_reset_themselves_this_way(sf):
    """In Settings, removing a method asks for the password. The reset must
    not be a way for someone at an open session to skip that."""
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await turn_on_totp(db, admin)

        refused = await _refused(users.reset_user_two_step(admin.id, db, admin))

        assert (refused.status_code, refused.detail) == (
            400, "Change your own two-step verification in Settings.")
        assert await status.is_on(db, admin.id) is True
        assert await _audited(db) == []


async def test_resetting_someone_who_does_not_exist(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        refused = await _refused(users.reset_user_two_step(uuid.uuid4(), db, admin))

        assert refused.status_code == 404 and await _audited(db) == []


# ── the server command ──────────────────────────────────────────────────

async def test_the_command_resets_by_email_whatever_its_case(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await turn_on_totp(db, admin)
        await db.commit()

    found, message = await command.reset_by_email(sf, f"  {admin.email.upper()} ")

    assert found is True and admin.email in message and "is reset" in message
    async with sf() as db:
        assert await status.is_on(db, admin.id) is False
        assert (await db.get(User, admin.id)).hashed_password == admin.hashed_password
        assert await _audited(db) == [
            ("two_step_reset_from_server", None, admin.id, {"was_on": True})]


async def test_the_command_says_so_when_there_was_nothing_to_reset(sf):
    async with sf() as db:
        user = await add_user(db)
        await db.commit()

    found, message = await command.reset_by_email(sf, user.email)
    assert found is True and "had no two-step verification" in message


async def test_the_command_does_nothing_for_an_unknown_email(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await db.commit()

    found, message = await command.reset_by_email(sf, "nobody@example.com")

    assert found is False and "nobody@example.com" in message
    async with sf() as db:
        assert await status.is_on(db, user.id) is True


@pytest.mark.parametrize("argv", [[], ["a@example.com", "b@example.com"], [""], ["--help"]])
def test_the_command_wants_exactly_one_email(argv, capsys):
    assert command.main(argv) == 2
    assert "usage: python -m app.reset_two_step <email>" in capsys.readouterr().err
```

Create `backend/tests/test_two_step_http.py`:

```python
"""Two-step verification over real HTTP requests: what a browser sends and
gets back, with the routers mounted where the application mounts them."""
import os
import sys
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; some routers import a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import auth, system, two_step, two_step_account, users
from app.database import get_db
from tests._mcp import seed_token
from tests._passkey import SoftwareAuthenticator
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, PASSWORD, add_user, code_at

API = "/api/v1"


class FakeRedis:
    """Counts requests like the rate limiter's Redis would."""

    counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def ttl(self, key: str) -> int:
        return 42

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr("app.core.audit.async_session", factory)
    monkeypatch.setattr("app.services.two_step.clock.now", lambda: NOW)
    FakeRedis.counts = {}
    monkeypatch.setattr("app.core.rate_limit.aioredis.from_url", lambda *a, **k: FakeRedis())
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def http(sf):
    """A client for an app with the routers two-step verification touches,
    mounted as ``app/api/v1/router.py`` mounts them."""
    api = APIRouter()
    api.include_router(auth.router, prefix="/auth")
    api.include_router(two_step.router, prefix="/auth/two-step")
    api.include_router(two_step_account.router, prefix="/auth/two-step")
    api.include_router(system.router, prefix="/system")
    api.include_router(users.router, prefix="/users")
    app = FastAPI()
    app.include_router(api, prefix=API)

    async def _get_db():
        async with sf() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _account(sf, **kwargs):
    async with sf() as db:
        user = await add_user(db, **kwargs)
        await db.commit()
    return user


def _as(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _sign_in(http, user) -> dict:
    """Sign in an account that has no second step yet. Returns its headers."""
    response = await http.post(f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _as(response.json()["access_token"])


# ── a whole sign-in ─────────────────────────────────────────────────────

async def test_setting_up_an_authenticator_then_signing_in_with_it(sf, http):
    user = await _account(sf)
    session = await _sign_in(http, user)

    # Settings: scan, confirm, save the codes.
    setup = (await http.post(f"{API}/auth/two-step/totp", headers=session)).json()
    confirmed = await http.post(
        f"{API}/auth/two-step/totp/confirm", headers=session,
        json={"code": code_at(setup["secret"], NOW)})
    assert confirmed.status_code == 200 and len(confirmed.json()["recovery_codes"]) == 10
    summary = (await http.get(f"{API}/auth/two-step", headers=session)).json()
    assert summary["enabled"] is True and summary["recovery_codes"]["left"] == 10

    # The next sign-in: the password is no longer enough.
    password_step = await http.post(
        f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})
    assert password_step.status_code == 200
    body = password_step.json()
    assert set(body) == {"status", "ticket", "methods", "passkey_origin"}
    assert body["status"] == "two_step_required" and body["methods"] == ["totp"]

    # The confirming code is spent; a recovery code gets in and says what is left.
    spent = await http.post(
        f"{API}/auth/two-step/verify",
        json={"ticket": body["ticket"], "code": code_at(setup["secret"], NOW)})
    assert spent.status_code == 401 and spent.json() == {"detail": "That code is not right."}
    signed_in = await http.post(
        f"{API}/auth/two-step/verify",
        json={"ticket": body["ticket"], "recovery_code": confirmed.json()["recovery_codes"][0]})
    assert signed_in.status_code == 200
    assert set(signed_in.json()) == {
        "status", "access_token", "refresh_token", "token_type", "recovery_codes_left"}
    assert signed_in.json()["recovery_codes_left"] == 9

    me = (await http.get(f"{API}/auth/me", headers=_as(signed_in.json()["access_token"]))).json()
    assert me["email"] == user.email
    assert (me["two_step_enabled"], me["two_step_setup_required"]) == (True, False)


async def test_a_plain_sign_in_looks_as_it_always_did_plus_a_status(sf, http):
    user = await _account(sf)
    response = await http.post(f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})

    assert set(response.json()) == {"status", "access_token", "refresh_token", "token_type"}
    assert response.json()["status"] == "signed_in" and response.json()["token_type"] == "bearer"


async def test_a_passkey_added_in_settings_signs_in(sf, http):
    user = await _account(sf)
    session = await _sign_in(http, user)
    device = SoftwareAuthenticator()

    offered = (await http.post(f"{API}/auth/two-step/passkeys/options", headers=session)).json()
    added = await http.post(
        f"{API}/auth/two-step/passkeys", headers=session,
        json={"ticket": offered["ticket"], "credential": device.create(offered["options"]),
              "name": "Work laptop"})
    assert added.status_code == 200 and added.json()["passkey"]["name"] == "Work laptop"
    passkey_id = added.json()["passkey"]["id"]
    renamed = await http.patch(
        f"{API}/auth/two-step/passkeys/{passkey_id}", headers=session, json={"name": "Desk"})
    assert renamed.json()["name"] == "Desk"

    ticket = (await http.post(
        f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})).json()["ticket"]
    options = (await http.post(
        f"{API}/auth/two-step/passkey/options", json={"ticket": ticket})).json()["options"]
    signed_in = await http.post(
        f"{API}/auth/two-step/passkey/verify",
        json={"ticket": ticket, "credential": device.get(options)})
    assert signed_in.status_code == 200 and signed_in.json()["status"] == "signed_in"

    # Removing it needs the password, and turns two-step verification off.
    wrong = await http.post(
        f"{API}/auth/two-step/passkeys/{passkey_id}/remove", headers=session,
        json={"password": "not it"})
    assert wrong.status_code == 400 and wrong.json() == {"detail": "Wrong password."}
    removed = await http.post(
        f"{API}/auth/two-step/passkeys/{passkey_id}/remove", headers=session,
        json={"password": PASSWORD})
    assert removed.status_code == 200 and removed.json()["enabled"] is False


# ── when it is required ─────────────────────────────────────────────────

async def test_an_administrator_requires_it_and_a_user_sets_it_up_to_get_in(sf, http):
    admin, member = await _account(sf, is_admin=True), await _account(sf)
    admin_session = await _sign_in(http, admin)
    member_session = await _sign_in(http, member)
    member_refresh = (await http.post(
        f"{API}/auth/login", json={"email": member.email, "password": PASSWORD})).json()["refresh_token"]

    # Not before the administrator has it on.
    early = await http.put(f"{API}/system/security", headers=admin_session,
                           json={"require_two_step": True})
    assert early.status_code == 409
    setup = (await http.post(f"{API}/auth/two-step/totp", headers=admin_session)).json()
    await http.post(f"{API}/auth/two-step/totp/confirm", headers=admin_session,
                    json={"code": code_at(setup["secret"], NOW)})
    turned_on = await http.put(f"{API}/system/security", headers=admin_session,
                               json={"require_two_step": True})
    assert turned_on.json() == {"require_two_step": True, "users_total": 2, "users_with_two_step": 1}

    # The member's open session is told, and can no longer renew itself.
    me = (await http.get(f"{API}/auth/me", headers=member_session)).json()
    assert me["two_step_setup_required"] is True
    renewal = await http.post(f"{API}/auth/refresh", json={"refresh_token": member_refresh})
    assert renewal.status_code == 401

    # Signing in again leads through the setup; only its end gives a session.
    password_step = (await http.post(
        f"{API}/auth/login", json={"email": member.email, "password": PASSWORD})).json()
    assert set(password_step) == {"status", "ticket", "passkey_origin"}
    assert password_step["status"] == "two_step_setup_required"
    ticket = {"ticket": password_step["ticket"]}
    assert (await http.post(f"{API}/auth/two-step/setup/finish", json=ticket)).status_code == 410
    member_setup = (await http.post(f"{API}/auth/two-step/setup/totp", json=ticket)).json()
    codes = await http.post(
        f"{API}/auth/two-step/setup/totp/confirm",
        json={**ticket, "code": code_at(member_setup["secret"], NOW)})
    assert len(codes.json()["recovery_codes"]) == 10
    finished = await http.post(f"{API}/auth/two-step/setup/finish", json=ticket)
    assert finished.status_code == 200 and finished.json()["status"] == "signed_in"

    # The administrator sees it, and can reset the member, but not themselves.
    listed = (await http.get(f"{API}/users/", headers=admin_session)).json()
    assert {row["email"]: row["two_step_enabled"] for row in listed} == {
        admin.email: True, member.email: True}
    reset = await http.post(f"{API}/users/{member.id}/two-step/reset", headers=admin_session)
    assert reset.status_code == 204 and reset.content == b""
    own = await http.post(f"{API}/users/{admin.id}/two-step/reset", headers=admin_session)
    assert own.status_code == 400
    # A member cannot touch the switch or the reset.
    new_session = _as(finished.json()["access_token"])
    assert (await http.get(f"{API}/system/security", headers=new_session)).status_code == 403
    assert (await http.post(
        f"{API}/users/{admin.id}/two-step/reset", headers=new_session)).status_code == 403


# ── who may call what ───────────────────────────────────────────────────

async def test_the_settings_routes_need_a_session_and_refuse_an_api_token(sf, http):
    user = await _account(sf, is_admin=True)
    async with sf() as db:
        token = await seed_token(db, user.id)
        await db.commit()

    assert (await http.get(f"{API}/auth/two-step")).status_code == 401
    assert (await http.post(f"{API}/auth/two-step/totp")).status_code == 401
    for method, path in (("GET", ""), ("POST", "/totp"), ("POST", "/passkeys/options")):
        refused = await http.request(method, f"{API}/auth/two-step{path}", headers=_as(token))
        assert refused.status_code == 403, path
        assert "API token" in refused.json()["detail"]
    # The token still works where tokens are meant to.
    assert (await http.get(f"{API}/auth/me", headers=_as(token))).status_code == 200


@pytest.mark.parametrize("path, body", [
    ("/verify", {"ticket": "nonsense", "code": "123456"}),
    ("/passkey/options", {"ticket": "nonsense"}),
    ("/passkey/verify", {"ticket": "nonsense", "credential": {}}),
    ("/setup/totp", {"ticket": "nonsense"}),
    ("/setup/totp/confirm", {"ticket": "nonsense", "code": "123456"}),
    ("/setup/passkey/options", {"ticket": "nonsense"}),
    ("/setup/passkey", {"ticket": "nonsense", "credential": {}}),
    ("/setup/finish", {"ticket": "nonsense"}),
])
async def test_without_a_real_ticket_every_sign_in_route_says_gone(sf, http, path, body):
    response = await http.post(f"{API}/auth/two-step{path}", json=body)

    assert response.status_code == 410
    assert response.json() == {"detail": "That took too long. Sign in again."}


@pytest.mark.parametrize("body", [
    {}, {"code": "123456"}, {"ticket": "x" * 201, "code": "1"}, {"ticket": "t", "code": "9" * 65},
    {"ticket": ["a"]}, "not an object",
])
async def test_malformed_requests_are_rejected_before_anything_is_looked_up(sf, http, body):
    response = await http.post(f"{API}/auth/two-step/verify", json=body)
    assert response.status_code == 422


async def test_the_sign_in_routes_are_rate_limited_per_address(sf, http):
    statuses = [
        (await http.post(f"{API}/auth/two-step/verify",
                         json={"ticket": "nonsense", "code": "123456"})).status_code
        for _ in range(31)
    ]

    assert statuses[:30] == [410] * 30 and statuses[30] == 429


async def test_the_password_prompts_are_rate_limited_too(sf, http):
    user = await _account(sf)
    session = await _sign_in(http, user)

    statuses = [
        (await http.post(f"{API}/auth/two-step/recovery-codes", headers=session,
                         json={"password": "guess"})).status_code
        for _ in range(11)
    ]

    assert statuses[:10] == [400] * 10 and statuses[10] == 429
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_admin.py tests/test_two_step_http.py
```

Expected: a collection error for `test_two_step_admin.py`, `ImportError: cannot import name 'reset_two_step' from 'app'`.

- [ ] **Step 3: Write the server command**

Create `backend/app/reset_two_step.py`:

```python
"""Reset one account's two-step verification from the server.

    python -m app.reset_two_step <email>

For an administrator who has lost their phone, passkeys and recovery codes
when no other administrator is left to reset them from the Users page. Run it
where the backend runs, for example:

    docker compose exec backend python -m app.reset_two_step admin@example.com

It removes the account's authenticator app, passkeys and recovery codes, and
records that it did. The password is not changed.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from app.core.audit import record as audit_record
from app.models.user import User
from app.services.two_step.reset import reset


async def reset_by_email(session_factory, email: str) -> tuple[bool, str]:
    """Reset the account with *email*. Returns whether it was found, and what
    to tell the person at the keyboard."""
    wanted = email.strip().lower()
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(func.lower(User.email) == wanted))
        ).scalar_one_or_none()
        if user is None:
            return False, f"No account has the email {email.strip()}."
        was_on = await reset(db, user.id)
        await audit_record(
            action="two_step_reset_from_server",
            target_type="user",
            target_id=user.id,
            metadata={"was_on": was_on},
            db=db,
        )
        await db.commit()
    if was_on:
        return True, f"Two-step verification is reset for {user.email}. They can sign in with their password."
    return True, f"{user.email} had no two-step verification set up. Nothing else was changed."


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or not args[0].strip() or args[0].startswith("-"):
        print("usage: python -m app.reset_two_step <email>", file=sys.stderr)
        return 2
    from app.database import async_session

    found, message = asyncio.run(reset_by_email(async_session, args[0]))
    print(message, file=sys.stdout if found else sys.stderr)
    return 0 if found else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the switch, the column and the reset**

**`backend/app/api/v1/system.py`** — edit 1 of 2. Replace:

````python
from app.config import get_settings
from app.core.deps import get_current_active_user, get_current_admin_user
from app.database import get_db
from app.models.system_setting import SystemSetting
from app.models.user import User
````

with:

````python
from app.api.v1.backups import require_global_admin
from app.config import get_settings
from app.core.audit import record as audit_record
from app.core.deps import get_current_active_user, get_current_admin_user
from app.database import get_db
from app.models.system_setting import SystemSetting
from app.models.user import User
from app.schemas.two_step import SecuritySettings, SecuritySettingsUpdate
from app.services.two_step import status as two_step_status
````

**`backend/app/api/v1/system.py`** — edit 2 of 2. Replace:

````python
        except Exception:
            pass  # best-effort; builds will be picked up on the next cycle

    return result
````

with:

````python
        except Exception:
            pass  # best-effort; builds will be picked up on the next cycle

    return result


# ---------------------------------------------------------------------------
# Security: whether every account must use two-step verification
# ---------------------------------------------------------------------------

async def _security_settings(db: AsyncSession) -> dict:
    total, with_two_step = await two_step_status.counts(db)
    return {
        "require_two_step": await two_step_status.is_required(db),
        "users_total": total,
        "users_with_two_step": with_two_step,
    }


@router.get("/security", response_model=SecuritySettings)
async def get_security(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_global_admin),
) -> dict:
    return await _security_settings(db)


@router.put("/security", response_model=SecuritySettings)
async def set_security(
    body: SecuritySettingsUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_global_admin),
) -> dict:
    """Require two-step verification for every account, or stop requiring it.

    Only an administrator who has it on can turn the requirement on, so
    nobody requires it and then finds they cannot get in themselves.
    """
    if body.require_two_step and not await two_step_status.is_on(db, admin.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Set up two-step verification on your own account first.",
        )
    if body.require_two_step != await two_step_status.is_required(db):
        await two_step_status.set_required(db, body.require_two_step)
        await audit_record(
            action="two_step_requirement_changed",
            actor_id=admin.id,
            metadata={"required": body.require_two_step},
            db=db,
        )
    return await _security_settings(db)
````

**`backend/app/schemas/users.py`**. Replace:

````python
    updated_at: datetime | None = None
    roles: list[UserRoleInfo] = []
````

with:

````python
    updated_at: datetime | None = None
    roles: list[UserRoleInfo] = []
    two_step_enabled: bool = False
````

**`backend/app/api/v1/users.py`** — edit 1 of 7. Replace:

````python
from app.core.deps import require_permission
````

with:

````python
from app.core.audit import record as audit_record
from app.core.deps import require_permission
````

**`backend/app/api/v1/users.py`** — edit 2 of 7. Replace:

````python
    UserUpdateRequest,
)
````

with:

````python
    UserUpdateRequest,
)
from app.services.two_step import status as two_step_status
from app.services.two_step.reset import reset as reset_two_step
````

**`backend/app/api/v1/users.py`** — edit 3 of 7. Replace:

````python
def _user_to_detail(user: User, project_names: dict | None = None) -> dict:
````

with:

````python
def _user_to_detail(
    user: User, project_names: dict | None = None, two_step_enabled: bool = False
) -> dict:
````

**`backend/app/api/v1/users.py`** — edit 4 of 7. Replace:

````python
        "updated_at": user.updated_at,
        "roles": roles,
    }
````

with:

````python
        "updated_at": user.updated_at,
        "roles": roles,
        "two_step_enabled": two_step_enabled,
    }
````

**`backend/app/api/v1/users.py`** — edit 5 of 7. Replace:

````python
    pnames = await _project_name_map(db, users)
    return [_user_to_detail(u, pnames) for u in users]
````

with:

````python
    pnames = await _project_name_map(db, users)
    with_two_step = await two_step_status.enabled_user_ids(db)
    return [_user_to_detail(u, pnames, u.id in with_two_step) for u in users]
````

**`backend/app/api/v1/users.py`** — edit 6 of 7. Replace:

````python
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    pnames = await _project_name_map(db, [user])
    return _user_to_detail(user, pnames)
````

with:

````python
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    pnames = await _project_name_map(db, [user])
    return _user_to_detail(user, pnames, await two_step_status.is_on(db, user.id))
````

**`backend/app/api/v1/users.py`** — edit 7 of 7. Replace:

````python
    await db.commit()
    await db.refresh(user)
    pnames = await _project_name_map(db, [user])
    return _user_to_detail(user, pnames)
````

with:

````python
    await db.commit()
    await db.refresh(user)
    pnames = await _project_name_map(db, [user])
    return _user_to_detail(user, pnames, await two_step_status.is_on(db, user.id))


@router.post("/{user_id}/two-step/reset", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_two_step(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("users.manage")),
) -> None:
    """Remove a user's authenticator app, passkeys and recovery codes, for a
    user who has lost all of them. Their open sessions are left alone. If
    two-step verification is required they set it up again at the next
    sign-in."""
    if user_id == current_user.id:
        # Removing one's own methods asks for the password, in Settings.
        # This route must not be a way around that.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Change your own two-step verification in Settings.",
        )
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    was_on = await reset_two_step(db, user_id)
    await audit_record(
        action="two_step_reset_by_admin",
        actor_id=current_user.id,
        target_type="user",
        target_id=user_id,
        metadata={"was_on": was_on},
        db=db,
    )
````

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_admin.py tests/test_two_step_http.py
```

Expected: `41 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/reset_two_step.py backend/app/api/v1/system.py backend/app/api/v1/users.py backend/app/schemas/users.py backend/tests/test_two_step_admin.py backend/tests/test_two_step_http.py
git commit -m "feat(two-step): require it for everyone, see who has it, reset a user

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The registry (`docker login`)

**Files:**
- Modify: `backend/app/core/registry_auth.py`, `backend/app/api/v1/registry_oci.py`, `backend/app/core/token_scopes.py`, `backend/tests/test_token_scopes.py`
- Test: `backend/tests/test_two_step_registry.py` (new)

**Interfaces:**
- Consumes: `status.is_on`, `status.is_required` (Task 7); `authenticate_pat`, `effective_permissions` from `app.core.deps`; `is_pat` from `app.core.security`.
- Produces, in `app.core.registry_auth`: `TWO_STEP_MESSAGE`, `TOKEN_SCOPE_MESSAGE`, `RegistryLoginRefused(Exception)`; `authenticate_basic(db, username, password)` still returns `(subject, actor_id, actions)` or `(None, None, [])`, and now raises `RegistryLoginRefused` for a correct password that may not be used here, or a valid token without a registry scope.
- Produces: a token scope `container.registry` (`registry.read`, `registry.push`), last in the catalogue.
- Produces: `/v2/token` answers a refusal with 401 and a body carrying the message both as `details` and as `errors[0].message`. A real docker client prints it as `unauthorized: <message>`; this was checked against a stub registry while planning.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_registry.py`:

```python
"""``docker login``: the password alone is not enough for an account that uses
two-step verification, and an API token works in its place."""
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import registry_oci
from app.core import registry_auth
from app.core.registry_auth import RegistryLoginRefused, authenticate_basic, decode_registry_token
from app.models.registry import RegistryDeployToken
from app.models.user import User
from app.services.two_step import status
from tests._mcp import seed_member, seed_token
from tests._rbac import build_inmemory_factory
from tests._two_step import PASSWORD, add_passkey, add_user, cheap_hash, turn_on_totp

REFUSED = (None, None, [])
TWO_STEP = (
    "This account uses two-step verification. Use an API token as the password "
    "(Settings → API tokens)."
)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _member(db, permissions, *, password=PASSWORD, is_admin=False) -> User:
    user = await db.get(User, await seed_member(db, permissions, is_admin=is_admin))
    user.hashed_password = cheap_hash(password)
    await db.flush()
    return user


async def _message(call) -> str:
    with pytest.raises(RegistryLoginRefused) as refused:
        await call
    return str(refused.value)


# ── the account password ────────────────────────────────────────────────

async def test_the_password_still_works_for_an_account_without_two_step(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])

        assert await authenticate_basic(db, user.email, PASSWORD) == (
            str(user.id), user.id, ["pull", "push"])


@pytest.mark.parametrize("give_method", [turn_on_totp, add_passkey])
async def test_the_password_is_refused_once_the_account_has_a_method(sf, give_method):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        await give_method(db, user)

        assert await _message(authenticate_basic(db, user.email, PASSWORD)) == TWO_STEP
        # By name as well as by email.
        assert await _message(authenticate_basic(db, user.name, PASSWORD)) == TWO_STEP


async def test_the_password_is_refused_when_it_is_required_and_not_set_up(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        await status.set_required(db, True)

        assert await _message(authenticate_basic(db, user.email, PASSWORD)) == TWO_STEP


async def test_a_wrong_password_learns_nothing_about_the_account(sf):
    async with sf() as db:
        with_two_step = await _member(db, ["registry.read"])
        await turn_on_totp(db, with_two_step)
        without = await _member(db, ["registry.read"])

        for user in (with_two_step, without):
            assert await authenticate_basic(db, user.email, "wrong") == REFUSED
        assert await authenticate_basic(db, "nobody@example.com", PASSWORD) == REFUSED


async def test_an_account_without_a_password_is_refused_not_a_crash(sf):
    async with sf() as db:
        user = await add_user(db, password=None)
        assert await authenticate_basic(db, user.email, "anything") == REFUSED


async def test_a_deactivated_account_is_refused_as_before(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        user.is_active = False
        await db.flush()

        assert await authenticate_basic(db, user.email, PASSWORD) == REFUSED


# ── a deploy token ──────────────────────────────────────────────────────

async def test_a_deploy_token_works_as_before_even_when_two_step_is_required(sf):
    async with sf() as db:
        owner = await add_user(db)
        token = RegistryDeployToken(
            id=uuid.uuid4(), name="ci", token_hash=cheap_hash("deploy-secret"),
            token_hint="deploy", scope="push", is_active=True, created_by=owner.id,
        )
        db.add(token)
        await status.set_required(db, True)
        await db.flush()

        assert await authenticate_basic(db, "deploy-token", "deploy-secret") == (
            f"deploy-token:{token.id}", None, ["pull", "push"])
        assert await authenticate_basic(db, "deploy-token", "wrong") == REFUSED


# ── an API token as the password ────────────────────────────────────────

async def test_a_full_access_token_stands_for_the_account(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        token = await seed_token(db, user.id)

        assert await authenticate_basic(db, user.email, token) == (
            str(user.id), user.id, ["pull", "push"])


async def test_the_token_says_who_it_is_whatever_the_username(sf):
    async with sf() as db:
        owner = await _member(db, ["registry.read"])
        other = await _member(db, ["registry.read", "registry.push", "registry.manage"])
        token = await seed_token(db, owner.id)

        for username in ("", "anything", other.email):
            assert await authenticate_basic(db, username, token) == (
                str(owner.id), owner.id, ["pull"])


@pytest.mark.parametrize("give_method", [turn_on_totp, add_passkey])
async def test_a_token_works_for_an_account_with_two_step(sf, give_method):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        await give_method(db, user)
        await status.set_required(db, True)
        token = await seed_token(db, user.id)

        assert await authenticate_basic(db, user.email, token) == (
            str(user.id), user.id, ["pull", "push"])


@pytest.mark.parametrize("scopes, actions", [
    (["read.only"], ["pull"]),
    (["container.registry"], ["pull", "push"]),
    (["container.registry", "coding.agent"], ["pull", "push"]),
])
async def test_a_scoped_token_may_do_what_its_scope_says(sf, scopes, actions):
    async with sf() as db:
        admin = await _member(db, [], is_admin=True)
        token = await seed_token(db, admin.id, scopes=scopes)

        assert await authenticate_basic(db, admin.email, token) == (str(admin.id), admin.id, actions)


async def test_no_scope_lets_a_token_delete_images(sf):
    async with sf() as db:
        admin = await _member(db, [], is_admin=True)
        every_scope = ["artifacts.download", "automate.workflows", "read.only", "coding.agent",
                       "container.registry"]
        token = await seed_token(db, admin.id, scopes=every_scope)

        _, _, actions = await authenticate_basic(db, admin.email, token)
        assert actions == ["pull", "push"]


async def test_a_scope_never_gives_more_than_the_account_has(sf):
    async with sf() as db:
        reader = await _member(db, ["registry.read"])
        token = await seed_token(db, reader.id, scopes=["container.registry"])

        assert await authenticate_basic(db, reader.email, token) == (str(reader.id), reader.id, ["pull"])


def test_the_registry_scope_is_pull_and_push():
    from app.core.token_scopes import TOKEN_SCOPES

    assert TOKEN_SCOPES["container.registry"]["permissions"] == {"registry.read", "registry.push"}


@pytest.mark.parametrize("scopes", [["coding.agent"], ["artifacts.download"], ["automate.workflows"]])
async def test_a_token_scoped_to_something_else_is_refused_with_the_reason(sf, scopes):
    async with sf() as db:
        admin = await _member(db, [], is_admin=True)
        token = await seed_token(db, admin.id, scopes=scopes)

        message = await _message(authenticate_basic(db, admin.email, token))
        assert message == "This API token's scope does not include the registry."


async def test_tokens_that_are_not_valid_are_refused_like_a_wrong_password(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        expired = await seed_token(db, user.id, expires_at=yesterday)
        revoked = await seed_token(db, user.id, is_active=False)

        for token in (expired, revoked, "megci_pat_" + "x" * 44):
            assert await authenticate_basic(db, user.email, token) == REFUSED


async def test_a_token_of_a_deactivated_account_is_refused(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        token = await seed_token(db, user.id)
        user.is_active = False
        await db.flush()

        assert await authenticate_basic(db, user.email, token) == REFUSED


# ── the token endpoint, as docker calls it ──────────────────────────────

def _docker_asks(username: str, password: str) -> Request:
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    return Request({
        "type": "http", "method": "GET", "path": "/v2/token", "query_string": b"",
        "headers": [(b"authorization", f"Basic {basic}".encode())],
    })


async def test_docker_is_told_what_to_do_instead(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        await turn_on_totp(db, user)

        response = await registry_oci.get_token(_docker_asks(user.email, PASSWORD), "", "", db)

        assert isinstance(response, JSONResponse) and response.status_code == 401
        body = json.loads(response.body)
        # Both shapes the docker client reads a message from.
        assert body["details"] == TWO_STEP
        assert body["errors"] == [{"code": "UNAUTHORIZED", "message": TWO_STEP}]


async def test_docker_gets_a_registry_token_for_an_api_token(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        await turn_on_totp(db, user)
        token = await seed_token(db, user.id)

        response = await registry_oci.get_token(_docker_asks(user.email, token), "", "", db)

        granted = decode_registry_token(response["token"])
        assert granted["sub"] == str(user.id) and granted["access"] == ["pull", "push"]


async def test_docker_gets_the_usual_refusal_for_a_wrong_password(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        await turn_on_totp(db, user)

        with pytest.raises(HTTPException) as refused:
            await registry_oci.get_token(_docker_asks(user.email, "wrong"), "", "", db)
        assert (refused.value.status_code, refused.value.detail) == (401, "Invalid credentials")


def test_the_messages_are_the_ones_the_design_gives():
    assert registry_auth.TWO_STEP_MESSAGE == TWO_STEP
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_registry.py
```

Expected: a collection error, `ImportError: cannot import name 'RegistryLoginRefused' from 'app.core.registry_auth'`.

- [ ] **Step 3: Add the scope, accept an API token, refuse the password**

The second edit below changes an existing test that pins the order of the scope catalogue.

**`backend/app/core/token_scopes.py`**. Replace:

````python
            "artifacts.read",
        }),
    },
}
````

with:

````python
            "artifacts.read",
        }),
    },
    "container.registry": {
        "label": "Container registry",
        "description": (
            "Pull and push images. Use the token as the password for docker login."
        ),
        "permissions": frozenset({"registry.read", "registry.push"}),
    },
}
````

**`backend/tests/test_token_scopes.py`**. Replace:

````python
        "read.only",
        "coding.agent",
    ]
````

with:

````python
        "read.only",
        "coding.agent",
        "container.registry",
    ]
````

**`backend/app/core/registry_auth.py`** — edit 1 of 5. Replace:

````python
This module handles credential validation for user logins (Basic auth
with username/password), deploy tokens, and build-scoped tokens.
"""
````

with:

````python
This module handles credential validation for user logins (Basic auth
with username/password), deploy tokens, and build-scoped tokens.

The registry has no second step. So an account that uses two-step
verification cannot log in here with its password: it uses an API token as
the password instead, which works for any account.
"""
````

**`backend/app/core/registry_auth.py`** — edit 2 of 5. Replace:

````python
from app.config import get_settings
from app.core.security import verify_password
from app.models.registry import RegistryDeployToken
from app.models.role import UserRole
from app.models.user import User
````

with:

````python
from app.config import get_settings
from app.core.deps import authenticate_pat, effective_permissions
from app.core.security import is_pat, verify_password
from app.models.registry import RegistryDeployToken
from app.models.role import UserRole
from app.models.user import User
from app.services.two_step import status as two_step_status
````

**`backend/app/core/registry_auth.py`** — edit 3 of 5. Replace:

````python
_REGISTRY_TOKEN_EXPIRY = timedelta(minutes=30)
````

with:

````python
_REGISTRY_TOKEN_EXPIRY = timedelta(minutes=30)

TWO_STEP_MESSAGE = (
    "This account uses two-step verification. Use an API token as the password "
    "(Settings → API tokens)."
)
TOKEN_SCOPE_MESSAGE = "This API token's scope does not include the registry."


class RegistryLoginRefused(Exception):
    """The credentials are who they say they are, but may not be used here.
    The message is safe to show: it is only raised for a correct password
    or a valid token, so it tells a stranger nothing about an account."""
````

**`backend/app/core/registry_auth.py`** — edit 4 of 5. Replace:

````python
    if username == "deploy-token":
        return await _auth_deploy_token(db, password)
````

with:

````python
    if username == "deploy-token":
        return await _auth_deploy_token(db, password)
    if is_pat(password):
        return await _auth_api_token(db, password)
````

**`backend/app/core/registry_auth.py`** — edit 5 of 5. Replace:

````python
    if not verify_password(password, user.hashed_password):
        return None, None, []

    actions = _user_actions(user)
    return str(user.id), user.id, actions
````

with:

````python
    if not user.hashed_password or not verify_password(password, user.hashed_password):
        return None, None, []

    # The password is right, and here it would be the only proof. Where the
    # account has a second step, or must set one up, that is not enough.
    if await two_step_status.is_on(db, user.id) or await two_step_status.is_required(db):
        raise RegistryLoginRefused(TWO_STEP_MESSAGE)

    actions = _user_actions(user)
    return str(user.id), user.id, actions


async def _auth_api_token(
    db: AsyncSession, token: str
) -> tuple[str | None, uuid.UUID | None, list[str]]:
    """An API token as the password. The token says who it is (the username
    is not checked), and its scope limits what it may do, as everywhere."""
    user = await authenticate_pat(db, token)
    if user is None or not user.is_active:
        return None, None, []
    if user.active_token_scopes is None:
        # A Full-access token stands for the account itself.
        return str(user.id), user.id, _user_actions(user)
    allowed = effective_permissions(user)
    actions = [
        action
        for action, permission in (
            ("pull", "registry.read"),
            ("push", "registry.push"),
            ("delete", "registry.manage"),
        )
        if permission in allowed
    ]
    if not actions:
        raise RegistryLoginRefused(TOKEN_SCOPE_MESSAGE)
    return str(user.id), user.id, actions
````

**`backend/app/api/v1/registry_oci.py`** — edit 1 of 4. Replace:

````python
from fastapi.responses import StreamingResponse
````

with:

````python
from fastapi.responses import JSONResponse, StreamingResponse
````

**`backend/app/api/v1/registry_oci.py`** — edit 2 of 4. Replace:

````python
from app.core.registry_auth import (
    authenticate_basic,
````

with:

````python
from app.core.registry_auth import (
    RegistryLoginRefused,
    authenticate_basic,
````

**`backend/app/api/v1/registry_oci.py`** — edit 3 of 4. Replace:

````python
@router.get("/v2/token")
@router.post("/v2/token")
async def get_token(
    request: Request,
    service: str = Query(""),
    scope: str = Query(""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    _registry_disabled_check()
````

with:

````python
def _login_refused(message: str) -> JSONResponse:
    """A refusal the docker client prints: it shows ``details`` (or the
    first error's message) after "unauthorized:"."""
    return JSONResponse(
        status_code=401,
        content={
            "errors": [{"code": "UNAUTHORIZED", "message": message}],
            "details": message,
        },
        headers={"Docker-Distribution-Api-Version": "registry/2.0"},
    )


@router.get("/v2/token", response_model=None)
@router.post("/v2/token", response_model=None)
async def get_token(
    request: Request,
    service: str = Query(""),
    scope: str = Query(""),
    db: AsyncSession = Depends(get_db),
) -> dict | JSONResponse:
    _registry_disabled_check()
````

**`backend/app/api/v1/registry_oci.py`** — edit 4 of 4. Replace:

````python
        username, _, password = decoded.partition(":")
        subject, actor_id, actions = await authenticate_basic(db, username, password)
        if subject is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    elif form_username and form_password:
        subject, actor_id, actions = await authenticate_basic(
            db, form_username, form_password
        )
        if subject is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
````

with:

````python
        username, _, password = decoded.partition(":")
        try:
            subject, actor_id, actions = await authenticate_basic(db, username, password)
        except RegistryLoginRefused as refused:
            return _login_refused(str(refused))
        if subject is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
    elif form_username and form_password:
        try:
            subject, actor_id, actions = await authenticate_basic(
                db, form_username, form_password
            )
        except RegistryLoginRefused as refused:
            return _login_refused(str(refused))
        if subject is None:
            raise HTTPException(status_code=401, detail="Invalid credentials")
````

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_registry.py tests/test_token_scopes.py
```

Expected: `39 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/core/registry_auth.py backend/app/api/v1/registry_oci.py backend/app/core/token_scopes.py backend/tests/test_token_scopes.py backend/tests/test_two_step_registry.py
git commit -m "feat(two-step): docker login takes an API token, not a password, where there is a second step

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Backups

**Files:**
- Modify: `backend/app/services/backup/tables.py`, `backend/app/services/backup/restore.py`, `backend/tests/_backup.py`, `backend/tests/test_backup_tables.py`
- Test: `backend/tests/test_two_step_backup.py` (new)

**Interfaces:**
- Consumes: the models (Task 2); the services, for the tests (Tasks 3 to 7).
- Produces: `user_two_step` (with `totp_secret_encrypted` declared encrypted), `user_passkeys` and `user_recovery_codes` in `tables.TABLES`, right after `users`. `two_step_tickets` is not configuration.
- Produces: `_keep_acting_admin` returns the id the restoring administrator's account has after the restore, and `_keep_acting_admin_two_step(db, rows, acting_user_id, account_id)` replaces the backup's two-step rows for that account with the ones in the database now.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_two_step_backup.py`:

```python
"""Two-step verification in backups: it travels with the accounts, and a
restore never changes it for the administrator who runs the restore."""
import base64
import copy
import os
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.config import get_settings
from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.models.user import User
from app.services.backup.export import export_configuration
from app.services.backup.restore import apply_configuration
from app.services.backup.tables import TABLES
from app.services.two_step import passkeys, recovery_codes, sign_in, status, totp
from app.services.two_step.reset import reset
from tests._backup import OTHER_SECRET_KEY, SECRET_KEY, decrypt, insert, seed_world
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, STEP, add_passkey, code_at, turn_on_totp


@pytest_asyncio.fixture
async def sf(monkeypatch):
    # The authenticator secret is encrypted with the server's key; the backup
    # helpers call that key SECRET_KEY.
    monkeypatch.setattr(get_settings(), "MEGOOCI_SECRET_KEY", SECRET_KEY)
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def other_server():
    """A second, empty server with another secret key."""
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _world(sf) -> dict:
    """The usual configuration, where the developer has every kind of method,
    set up for real so that codes and the passkey can be tried afterwards."""
    async with sf() as db:
        ids = await seed_world(db)
        dev = await db.get(User, ids["dev"])
        await reset(db, dev.id)  # the placeholders seed_world gives the developer
        secret, codes = await turn_on_totp(db, dev)
        device, _ = await add_passkey(db, dev, name="Dev laptop")
        await db.commit()
    return {**ids, "secret": secret, "codes": codes, "device": device}


async def _export(sf, key=SECRET_KEY) -> dict:
    async with sf() as db:
        payload, _ = await export_configuration(db, key)
    return payload


async def _restore(sf, payload, acting, key=SECRET_KEY) -> None:
    async with sf() as db:
        await apply_configuration(db, payload, key, acting_user_id=acting)
        await db.commit()


async def _passkey_works(db, user_id, device) -> bool:
    options, challenge = await passkeys.authentication_options(db, user_id)
    try:
        await passkeys.verify(db, user_id, device.get(options), challenge, NOW)
    except passkeys.PasskeyError:
        return False
    return True


# ── what a backup holds ─────────────────────────────────────────────────

def test_the_three_account_tables_are_backed_up_and_tickets_are_not():
    names = [spec.name for spec in TABLES]

    assert names.index("users") < names.index("user_two_step")
    assert {"user_two_step", "user_passkeys", "user_recovery_codes"} <= set(names)
    assert TwoStepTicket.__tablename__ not in names
    by_name = {spec.name: spec for spec in TABLES}
    assert by_name["user_two_step"].encrypted == ("totp_secret_encrypted",)


async def test_the_authenticator_secret_leaves_the_server_in_the_clear_inside_the_file(sf):
    """The backup file is encrypted as a whole with the passphrase. Inside it
    the secret is not tied to this server's key, so another server can use it."""
    world = await _world(sf)
    payload = await _export(sf)
    (row,) = payload["tables"]["user_two_step"]

    assert set(row["totp_secret_encrypted"]) == {"$enc"}
    assert base64.b64decode(row["totp_secret_encrypted"]["$enc"]).decode() == world["secret"]
    # Recovery codes stay hashed, in the backup as in the database.
    hashes = [code["code_hash"] for code in payload["tables"]["user_recovery_codes"]]
    assert len(hashes) == 10 and all(h.startswith("$2") for h in hashes)
    assert not any(code.replace("-", "") in repr(payload) for code in world["codes"])


async def test_a_pending_sign_in_is_not_in_a_backup(sf):
    world = await _world(sf)
    async with sf() as db:
        await sign_in.decide(db, await db.get(User, world["dev"]), NOW)
        await db.commit()

    assert "two_step_tickets" not in (await _export(sf))["tables"]


# ── restoring ───────────────────────────────────────────────────────────

async def test_everything_works_again_on_a_server_with_another_key(sf, other_server, monkeypatch):
    world = await _world(sf)
    async with sf() as db:
        await status.set_required(db, True)
        await db.commit()
    payload = await _export(sf)
    async with other_server() as db:
        bootstrap = await insert(db, User, email="bootstrap@example.com", is_admin=True, is_active=True)
        await db.commit()

    await _restore(other_server, payload, bootstrap, key=OTHER_SECRET_KEY)

    monkeypatch.setattr(get_settings(), "MEGOOCI_SECRET_KEY", OTHER_SECRET_KEY)
    async with other_server() as db:
        dev = world["dev"]
        stored = (await db.get(UserTwoStep, dev)).totp_secret_encrypted
        assert decrypt(stored, OTHER_SECRET_KEY) == world["secret"]
        assert await status.methods(db, dev) == ["totp", "passkey"]
        later = NOW + 5 * STEP
        assert await totp.check(db, dev, code_at(world["secret"], later), later) is True
        assert await _passkey_works(db, dev, world["device"]) is True
        assert await recovery_codes.use(db, dev, world["codes"][0], NOW) is True
        assert await status.is_required(db) is True


async def test_a_restore_puts_other_accounts_back_as_the_backup_has_them(sf):
    world = await _world(sf)
    payload = await _export(sf)
    async with sf() as db:
        dev = await db.get(User, world["dev"])
        await reset(db, dev.id)                      # since the backup: reset,
        new_secret, _ = await turn_on_totp(db, dev)  # set up again with another phone
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await totp.check(db, dev.id, code_at(new_secret, later), later) is False
        assert await totp.check(db, dev.id, code_at(world["secret"], later), later) is True
        assert await _passkey_works(db, dev.id, world["device"]) is True
        assert await recovery_codes.remaining(db, dev.id) == 10


async def test_a_restore_removes_what_was_added_since(sf):
    ids = await _world(sf)
    payload = await _export(sf)
    async with sf() as db:
        newcomer = await insert(db, User, email="new@example.com", is_active=True)
        await add_passkey(db, await db.get(User, newcomer))
        await db.commit()

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        owners = set((await db.execute(sa.select(UserPasskey.user_id))).scalars())
        assert owners == {ids["dev"]}
        assert (await db.execute(sa.select(UserRecoveryCode.user_id).distinct())).scalars().all() == [ids["dev"]]


# ── the administrator who restores ──────────────────────────────────────

async def test_the_restoring_administrator_keeps_two_step_the_backup_never_had(sf):
    world = await _world(sf)
    payload = await _export(sf)  # the administrator has nothing in it
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        secret, codes = await turn_on_totp(db, admin)
        device, _ = await add_passkey(db, admin)
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await status.methods(db, admin.id) == ["totp", "passkey"]
        assert await totp.check(db, admin.id, code_at(secret, later), later) is True
        assert await _passkey_works(db, admin.id, device) is True
        assert await recovery_codes.use(db, admin.id, codes[0], NOW) is True


async def test_the_restoring_administrator_is_not_handed_back_an_old_phone(sf):
    """In the backup they had an authenticator and a passkey. Since then they
    replaced the phone. Restoring must not ask for the old one."""
    world = await _world(sf)
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        old_secret, old_codes = await turn_on_totp(db, admin)
        old_device, _ = await add_passkey(db, admin)
        await db.commit()
    payload = await _export(sf)
    async with sf() as db:
        await reset(db, admin.id)
        new_secret, new_codes = await turn_on_totp(db, admin)
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await status.methods(db, admin.id) == ["totp"]
        assert await totp.check(db, admin.id, code_at(old_secret, later), later) is False
        assert await totp.check(db, admin.id, code_at(new_secret, later), later) is True
        assert await _passkey_works(db, admin.id, old_device) is False
        assert await recovery_codes.use(db, admin.id, old_codes[0], NOW) is False
        assert await recovery_codes.use(db, admin.id, new_codes[0], NOW) is True


async def test_the_restoring_administrator_who_has_none_is_not_given_any(sf):
    world = await _world(sf)
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        await turn_on_totp(db, admin)
        await db.commit()
    payload = await _export(sf)
    async with sf() as db:
        await reset(db, admin.id)
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        assert await status.is_on(db, admin.id) is False
        assert (await sign_in.decide(db, admin, NOW))["status"] == "signed_in"


async def test_two_step_follows_an_administrator_the_backup_knows_under_another_id(sf):
    """The server was rebuilt since the backup: same people, new ids."""
    world = await _world(sf)
    payload = await _export(sf)
    old_id = str(uuid.uuid4())
    payload["tables"]["users"].append({
        **copy.deepcopy(payload["tables"]["users"][0]), "id": old_id, "email": "Ops@Example.com",
    })
    # In the backup that account had an authenticator of its own.
    theirs = copy.deepcopy(payload["tables"]["user_two_step"][0])
    payload["tables"]["user_two_step"].append({**theirs, "user_id": old_id})
    async with sf() as db:
        acting_id = await insert(db, User, email="ops@example.com", is_admin=True, is_active=True)
        acting = await db.get(User, acting_id)
        secret, codes = await turn_on_totp(db, acting)
        device, _ = await add_passkey(db, acting)
        await db.commit()

    await _restore(sf, payload, acting_id)

    async with sf() as db:
        account = uuid.UUID(old_id)
        later = NOW + 5 * STEP
        assert await db.get(User, acting_id) is None, "the backup's account is the one that exists"
        assert await status.methods(db, account) == ["totp", "passkey"]
        assert await totp.check(db, account, code_at(secret, later), later) is True
        assert await totp.check(db, account, code_at(world["secret"], later), later) is False
        assert await _passkey_works(db, account, device) is True
        assert await recovery_codes.use(db, account, codes[0], NOW) is True
        # The developer is untouched by all that.
        assert await totp.check(db, world["dev"], code_at(world["secret"], later), later) is True


async def test_a_passkey_the_administrator_holds_now_is_not_given_to_someone_else(sf):
    """The same security key: in the backup it was the developer's; today it
    is registered to the administrator who restores."""
    world = await _world(sf)
    payload = await _export(sf)
    key_in_backup = payload["tables"]["user_passkeys"][0]["credential_id"]
    async with sf() as db:
        await reset(db, world["dev"])
        admin = await db.get(User, world["admin"])
        db.add(UserPasskey(
            user_id=admin.id, credential_id=world["device"].credential_id,
            public_key=b"moved", name="Now the admin's",
        ))
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        held = (await db.execute(sa.select(UserPasskey))).scalars().all()
        assert [(key.user_id, key.name) for key in held] == [(admin.id, "Now the admin's")]
        assert base64.b64encode(held[0].credential_id).decode() == key_in_backup
        # The developer still has the authenticator from the backup.
        assert await status.methods(db, world["dev"]) == ["totp"]


async def test_a_restore_that_fails_changes_no_ones_two_step(sf):
    world = await _world(sf)
    payload = await _export(sf)
    payload["tables"]["user_passkeys"].append({
        **copy.deepcopy(payload["tables"]["user_passkeys"][0]),
        "id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),  # no such user
    })
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        secret, _ = await turn_on_totp(db, admin)
        await db.commit()

    async with sf() as db:
        with pytest.raises(Exception):
            await apply_configuration(db, payload, SECRET_KEY, acting_user_id=world["admin"])
        await db.rollback()

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await totp.check(db, admin.id, code_at(secret, later), later) is True
        assert await status.methods(db, world["dev"]) == ["totp", "passkey"]
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_backup.py
```

Expected: `7 failed, 5 passed`; the failures are a `KeyError` for a two-step table that is not in the payload, and assertions that a restore left two-step verification as it was. (The five that pass describe things a restore that ignores these tables also leaves alone.)

- [ ] **Step 3: Put the tables in backups and keep the restoring administrator's**

The first two files are tests: the world every backup test starts from gains two-step rows for the developer, and the test that sorts every table into configuration or history learns the new ones.

**`backend/tests/_backup.py`** — edit 1 of 2. Replace:

````python
    from app.models.trigger import Trigger, WebhookEndpoint
    from app.models.user import User
````

with:

````python
    from app.models.trigger import Trigger, WebhookEndpoint
    from app.models.two_step import UserPasskey, UserRecoveryCode, UserTwoStep
    from app.models.user import User
````

**`backend/tests/_backup.py`** — edit 2 of 2. Replace:

````python
    ids["role"] = await insert(db, Role, name="developer", permissions=["pipelines.manage"])
````

with:

````python
    # The developer signs in with two-step verification.
    ids["two_step"] = await insert(
        db, UserTwoStep, user_id=ids["dev"],
        totp_secret_encrypted=encrypt("JBSWY3DPEHPK3PXP", secret_key),
        totp_confirmed_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    ids["passkey"] = await insert(
        db, UserPasskey, user_id=ids["dev"], credential_id=b"credential-id",
        public_key=b"public-key", transports=["usb"], name="Dev key")
    ids["recovery_code"] = await insert(
        db, UserRecoveryCode, user_id=ids["dev"], code_hash="$2b$04$a-hash-not-a-code")
    ids["role"] = await insert(db, Role, name="developer", permissions=["pipelines.manage"])
````

**`backend/tests/test_backup_tables.py`** — edit 1 of 2. Replace:

````python
    "container_repositories", "container_images", "container_tags", "registry_events",
}
````

with:

````python
    "container_repositories", "container_images", "container_tags", "registry_events",
    # A sign-in in progress: gone in minutes, and no use to another server.
    "two_step_tickets",
}
# Binary columns that hold nothing secret and are carried over as they are.
PLAIN_BINARY = {"user_passkeys": {"credential_id", "public_key"}}
````

**`backend/tests/test_backup_tables.py`** — edit 2 of 2. Replace:

````python
        binary = {c.name for c in spec.table.columns if isinstance(c.type, sa.LargeBinary)}
````

with:

````python
        binary = {c.name for c in spec.table.columns if isinstance(c.type, sa.LargeBinary)}
        binary -= PLAIN_BINARY.get(spec.name, set())
````

**`backend/app/services/backup/tables.py`** — edit 1 of 2. Replace:

````python
from app.models.trigger import Trigger, WebhookEndpoint
from app.models.user import User
````

with:

````python
from app.models.trigger import Trigger, WebhookEndpoint
from app.models.two_step import UserPasskey, UserRecoveryCode, UserTwoStep
from app.models.user import User
````

**`backend/app/services/backup/tables.py`** — edit 2 of 2. Replace:

````python
TABLES: tuple[TableSpec, ...] = (
    TableSpec(User.__table__),
````

with:

````python
TABLES: tuple[TableSpec, ...] = (
    TableSpec(User.__table__),
    # Two-step verification travels with the accounts, so a restore does not
    # quietly turn it off for everyone. Tickets are not configuration.
    TableSpec(UserTwoStep.__table__, encrypted=("totp_secret_encrypted",)),
    TableSpec(UserPasskey.__table__),
    TableSpec(UserRecoveryCode.__table__),
````

**`backend/app/services/backup/restore.py`** — edit 1 of 4. Replace:

````python
from app.models.build import Build, Stage, Step
from app.models.user import User
````

with:

````python
from app.models.build import Build, Stage, Step
from app.models.two_step import UserPasskey, UserRecoveryCode, UserTwoStep
from app.models.user import User
````

**`backend/app/services/backup/restore.py`** — edit 2 of 4. Replace:

````python
async def _keep_acting_admin(
    db: AsyncSession, users: list[dict[str, Any]], acting_user_id: uuid.UUID
) -> None:
    """The administrator who restores is never locked out: their account
    keeps its current password and stays an active administrator. Everything
    else about it comes from the backup, when the backup has it."""
    table = User.__table__
    current = (
        await db.execute(sa.select(table).where(table.c.id == acting_user_id))
    ).mappings().first()
    if current is None:
        return
````

with:

````python
async def _keep_acting_admin(
    db: AsyncSession, users: list[dict[str, Any]], acting_user_id: uuid.UUID
) -> uuid.UUID | None:
    """The administrator who restores is never locked out: their account
    keeps its current password and stays an active administrator. Everything
    else about it comes from the backup, when the backup has it.

    Returns the id the account has after the restore: the backup may know
    it, by its email, under another id.
    """
    table = User.__table__
    current = (
        await db.execute(sa.select(table).where(table.c.id == acting_user_id))
    ).mappings().first()
    if current is None:
        return None
````

**`backend/app/services/backup/restore.py`** — edit 3 of 4. Replace:

````python
    account["hashed_password"] = current["hashed_password"]
    account["is_admin"] = True
    account["is_active"] = True
````

with:

````python
    account["hashed_password"] = current["hashed_password"]
    account["is_admin"] = True
    account["is_active"] = True
    return account["id"]


_TWO_STEP_TABLES = (UserTwoStep.__table__, UserPasskey.__table__, UserRecoveryCode.__table__)


async def _keep_acting_admin_two_step(
    db: AsyncSession,
    rows: dict[str, list[dict[str, Any]]],
    acting_user_id: uuid.UUID,
    account_id: uuid.UUID,
) -> None:
    """The administrator who restores also keeps their two-step verification
    exactly as it is now: authenticator app, passkeys and recovery codes,
    whatever the backup holds for the account. An old backup must not ask
    them for a second step they no longer have, nor take away the one that
    protects the account today."""
    for table in _TWO_STEP_TABLES:
        current = (
            await db.execute(sa.select(table).where(table.c.user_id == acting_user_id))
        ).mappings().all()
        from_backup = [
            row for row in rows[table.name]
            if row["user_id"] not in (account_id, acting_user_id)
        ]
        rows[table.name] = from_backup + [{**row, "user_id": account_id} for row in current]

    # A passkey belongs to one account. Where the backup gives one of the
    # kept passkeys to somebody else, it stays with the administrator who
    # holds it now.
    passkeys = rows[UserPasskey.__tablename__]
    kept = {bytes(row["credential_id"]) for row in passkeys if row["user_id"] == account_id}
    rows[UserPasskey.__tablename__] = [
        row for row in passkeys
        if row["user_id"] == account_id or bytes(row["credential_id"]) not in kept
    ]
````

**`backend/app/services/backup/restore.py`** — edit 4 of 4. Replace:

````python
    await _keep_acting_admin(db, rows[User.__tablename__], acting_user_id)
````

with:

````python
    account_id = await _keep_acting_admin(db, rows[User.__tablename__], acting_user_id)
    if account_id is not None:
        await _keep_acting_admin_two_step(db, rows, acting_user_id, account_id)
````

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_two_step_backup.py tests/test_backup_tables.py tests/test_backup_restore.py
```

Expected: `41 passed`.

- [ ] **Step 5: Run the whole backend suite**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `1249 passed`.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/backup/tables.py backend/app/services/backup/restore.py backend/tests/_backup.py backend/tests/test_backup_tables.py backend/tests/test_two_step_backup.py
git commit -m "feat(two-step): backups carry it, and a restore leaves the restoring administrator's alone

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Frontend — signing in

There is no frontend test harness. The gate for the three frontend tasks is the type checker, and a production build at the end of Task 15.

**Files:**
- Create: `frontend/src/lib/passkeys.ts`, `frontend/src/components/two-step/authenticator-setup.tsx`, `frontend/src/components/two-step/recovery-codes.tsx`, `frontend/src/components/login/second-step.tsx`, `frontend/src/components/login/two-step-setup.tsx`
- Modify: `frontend/package.json`, `frontend/package-lock.json`, `frontend/src/lib/api.ts`, `frontend/src/lib/auth.ts`, `frontend/src/components/login/sign-in-form.tsx`, `frontend/src/app/login/page.tsx`, `frontend/src/app/signup/page.tsx`, `frontend/src/app/invite/accept/page.tsx`

**Interfaces:**
- Consumes: the routes of Tasks 8 to 10; `AuthCard` from `components/login/auth-card.tsx`.
- Produces, in `lib/api.ts`: `errorStatus(error) -> number | null`; the types `SignInResult`, `TwoStepMethod`, `PasskeyJson`, `PasskeyInfo`, `TwoStepSummary`, `TotpSetup`, `RecoveryCodes`, `SecuritySettings`; `twoStepApi`; `systemApi.security()` and `systemApi.setSecurity(require)`; `usersApi.resetTwoStep(id)`; `two_step_enabled` on `User` and `UserDetail`, `two_step_setup_required` on `User`. `authApi.login`, `authApi.signup` and `invitesApi.accept` return a `SignInResult`.
- Produces, in `lib/auth.ts`: `PendingTwoStep` (`kind`, `ticket`, `methods`, `passkeyOrigin`, `email`); in the store, `pending`, `acceptSignIn(result, email) -> Promise<SignInResult>`, `clearPending()`; `login` and `signup` resolve to the `SignInResult`.
- Produces, in `lib/passkeys.ts`: `passkeyUnavailableReason(passkeyOrigin) -> string | null`, `createPasskey(options)`, `signInWithPasskey(options)`, `passkeyPromptDismissed(error)`, `passkeyAlreadyRegistered(error)`.
- Produces: `AuthenticatorSetup({ setup, code, onCodeChange, className })`, `RecoveryCodes({ codes, className })`, `SecondStep({ pending })`, `TwoStepSetup({ pending })`.

- [ ] **Step 1: Add the two libraries**

Run from `frontend/`:

```bash
npm install --no-audit --no-fund "@simplewebauthn/browser@^14.0.0" "qrcode@^1.5.4"
npm install --no-audit --no-fund -D "@types/qrcode@^1.5.6"
```

Expected: `package.json` gains the three entries and `package-lock.json` changes.

- [ ] **Step 2: Teach the API client and the store about a sign-in that is not finished**

**`frontend/src/lib/api.ts`** — edit 1 of 9. Replace:

````ts
class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message || `API error: ${status}`);
    this.name = "ApiError";
  }
}
````

with:

````ts
class ApiError extends Error {
  constructor(
    public status: number,
    public body: unknown,
    message?: string,
  ) {
    super(message || `API error: ${status}`);
    this.name = "ApiError";
  }
}

/** The HTTP status an API call failed with, or null for any other error. */
export function errorStatus(error: unknown): number | null {
  return error instanceof ApiError ? error.status : null;
}
````

**`frontend/src/lib/api.ts`** — edit 2 of 9. Replace:

````ts
export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}
````

with:

````ts
export interface AuthTokens {
  access_token: string;
  refresh_token: string;
  token_type: string;
}

export type TwoStepMethod = "totp" | "passkey";

/**
 * The answer to a correct password. Only `signed_in` carries a session; the
 * other two carry a ticket for the second step, or for setting two-step
 * verification up when the server requires it.
 */
export type SignInResult =
  | (AuthTokens & { status: "signed_in"; recovery_codes_left?: number })
  | {
      status: "two_step_required";
      ticket: string;
      methods: TwoStepMethod[];
      passkey_origin: string;
    }
  | { status: "two_step_setup_required"; ticket: string; passkey_origin: string };

/** WebAuthn options and answers, as JSON. The browser library gives them shape. */
export type PasskeyJson = Record<string, unknown>;

export interface PasskeyInfo {
  id: string;
  name: string;
  created_at: string | null;
  last_used_at: string | null;
}

export interface TwoStepSummary {
  enabled: boolean;
  required: boolean;
  totp: { confirmed_at: string | null; last_used_at: string | null } | null;
  passkeys: PasskeyInfo[];
  recovery_codes: { left: number; generated_at: string | null } | null;
  passkey_origin: string;
}

export interface TotpSetup {
  secret: string;
  otpauth_uri: string;
}

/** Ten new codes, or null when the account already had its codes. */
export interface RecoveryCodes {
  recovery_codes: string[] | null;
}

export interface SecuritySettings {
  require_two_step: boolean;
  users_total: number;
  users_with_two_step: number;
}
````

**`frontend/src/lib/api.ts`** — edit 3 of 9. Replace:

````ts
  role: string | null;
  permissions: string[];
}

export const authApi = {
  signup: (data: SignupRequest) =>
    fetchApi<AuthTokens>("/api/v1/auth/signup", {
````

with:

````ts
  role: string | null;
  permissions: string[];
  two_step_enabled: boolean;
  /** The server requires two-step verification and this account has none. */
  two_step_setup_required: boolean;
}

export const authApi = {
  signup: (data: SignupRequest) =>
    fetchApi<SignInResult>("/api/v1/auth/signup", {
````

**`frontend/src/lib/api.ts`** — edit 4 of 9. Replace:

````ts
  login: (data: LoginRequest) =>
    fetchApi<AuthTokens>("/api/v1/auth/login", {
````

with:

````ts
  login: (data: LoginRequest) =>
    fetchApi<SignInResult>("/api/v1/auth/login", {
````

**`frontend/src/lib/api.ts`** — edit 5 of 9. Replace:

````ts
  updateProfile: (data: { name?: string; email?: string }) =>
    fetchApi<User>("/api/v1/auth/update-profile", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};
````

with:

````ts
  updateProfile: (data: { name?: string; email?: string }) =>
    fetchApi<User>("/api/v1/auth/update-profile", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};

// ------------------------------------------------------------------
// Two-step verification
// ------------------------------------------------------------------
const TWO_STEP = "/api/v1/auth/two-step";

function postTwoStep<T>(path: string, body: unknown = {}): Promise<T> {
  return fetchApi<T>(`${TWO_STEP}${path}`, { method: "POST", body: JSON.stringify(body) });
}

export const twoStepApi = {
  // While signing in: each call carries the ticket the password step gave.
  verify: (ticket: string, answer: { code: string } | { recovery_code: string }) =>
    postTwoStep<SignInResult>("/verify", { ticket, ...answer }),
  passkeyOptions: (ticket: string) =>
    postTwoStep<{ options: PasskeyJson }>("/passkey/options", { ticket }),
  passkeyVerify: (ticket: string, credential: PasskeyJson) =>
    postTwoStep<SignInResult>("/passkey/verify", { ticket, credential }),

  // The forced setup, when the server requires two-step verification.
  setupTotp: (ticket: string) => postTwoStep<TotpSetup>("/setup/totp", { ticket }),
  setupTotpConfirm: (ticket: string, code: string) =>
    postTwoStep<RecoveryCodes>("/setup/totp/confirm", { ticket, code }),
  setupPasskeyOptions: (ticket: string) =>
    postTwoStep<{ options: PasskeyJson }>("/setup/passkey/options", { ticket }),
  setupPasskey: (ticket: string, credential: PasskeyJson, name: string) =>
    postTwoStep<RecoveryCodes>("/setup/passkey", { ticket, credential, name }),
  setupFinish: (ticket: string) => postTwoStep<SignInResult>("/setup/finish", { ticket }),

  // One's own account, from Settings.
  summary: () => fetchApi<TwoStepSummary>(TWO_STEP),
  beginTotp: () => postTwoStep<TotpSetup>("/totp"),
  confirmTotp: (code: string) => postTwoStep<RecoveryCodes>("/totp/confirm", { code }),
  removeTotp: (password: string) =>
    postTwoStep<TwoStepSummary>("/totp/remove", { password }),
  passkeyRegistrationOptions: () =>
    postTwoStep<{ options: PasskeyJson; ticket: string }>("/passkeys/options"),
  addPasskey: (ticket: string, credential: PasskeyJson, name: string) =>
    postTwoStep<{ passkey: PasskeyInfo; recovery_codes: string[] | null }>("/passkeys", {
      ticket,
      credential,
      name,
    }),
  renamePasskey: (id: string, name: string) =>
    fetchApi<PasskeyInfo>(`${TWO_STEP}/passkeys/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),
  removePasskey: (id: string, password: string) =>
    postTwoStep<TwoStepSummary>(`/passkeys/${id}/remove`, { password }),
  regenerateRecoveryCodes: (password: string) =>
    postTwoStep<RecoveryCodes>("/recovery-codes", { password }),
};
````

**`frontend/src/lib/api.ts`** — edit 6 of 9. Replace:

````ts
  setMaintenance: (data: { enabled: boolean; message?: string | null }) =>
    fetchApi<MaintenanceInfo>("/api/v1/system/maintenance", {
      method: "PUT",
      body: JSON.stringify(data),
    }),
};
````

with:

````ts
  setMaintenance: (data: { enabled: boolean; message?: string | null }) =>
    fetchApi<MaintenanceInfo>("/api/v1/system/maintenance", {
      method: "PUT",
      body: JSON.stringify(data),
    }),

  security: () => fetchApi<SecuritySettings>("/api/v1/system/security"),

  setSecurity: (require_two_step: boolean) =>
    fetchApi<SecuritySettings>("/api/v1/system/security", {
      method: "PUT",
      body: JSON.stringify({ require_two_step }),
    }),
};
````

**`frontend/src/lib/api.ts`** — edit 7 of 9. Replace:

````ts
  updated_at: string | null;
  roles: UserRoleInfo[];
}
````

with:

````ts
  updated_at: string | null;
  roles: UserRoleInfo[];
  two_step_enabled: boolean;
}
````

**`frontend/src/lib/api.ts`** — edit 8 of 9. Replace:

````ts
  delete: (id: string) =>
    fetchApi<void>(`/api/v1/users/${id}`, { method: "DELETE" }),
};
````

with:

````ts
  delete: (id: string) =>
    fetchApi<void>(`/api/v1/users/${id}`, { method: "DELETE" }),

  /** Remove a user's authenticator app, passkeys and recovery codes. */
  resetTwoStep: (id: string) =>
    fetchApi<void>(`/api/v1/users/${id}/two-step/reset`, { method: "POST" }),
};
````

**`frontend/src/lib/api.ts`** — edit 9 of 9. Replace:

````ts
  accept: (data: AcceptInviteRequest) =>
    fetchApi<AuthTokens>("/api/v1/invites/accept", {
````

with:

````ts
  accept: (data: AcceptInviteRequest) =>
    fetchApi<SignInResult>("/api/v1/invites/accept", {
````

**`frontend/src/lib/auth.ts`** — edit 1 of 5. Replace:

````ts
import { authApi, onTokensChanged, type User, type AuthTokens } from "./api";
````

with:

````ts
import {
  authApi,
  onTokensChanged,
  type AuthTokens,
  type SignInResult,
  type TwoStepMethod,
  type User,
} from "./api";
````

**`frontend/src/lib/auth.ts`** — edit 2 of 5. Replace:

````ts
interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  hasHydrated: boolean;

  hydrate: () => void;
  login: (email: string, password: string) => Promise<void>;
  signup: (email: string, password: string, name: string) => Promise<void>;
````

with:

````ts
/**
 * A sign-in that has got as far as a correct password and now waits for the
 * second step ("verify"), or for two-step verification to be set up because
 * the server requires it ("setup"). Kept in memory only: a reload forgets it
 * and starts again from the password.
 */
export interface PendingTwoStep {
  kind: "verify" | "setup";
  ticket: string;
  methods: TwoStepMethod[];
  passkeyOrigin: string;
  email: string;
}

interface AuthState {
  user: User | null;
  accessToken: string | null;
  refreshToken: string | null;
  isLoading: boolean;
  hasHydrated: boolean;
  pending: PendingTwoStep | null;

  hydrate: () => void;
  login: (email: string, password: string) => Promise<SignInResult>;
  signup: (email: string, password: string, name: string) => Promise<SignInResult>;
  /**
   * Take what the server answered to a password, a second step or a finished
   * setup: a session is stored and the user loaded; anything else becomes
   * `pending`. Returns the answer, so the caller can tell which it was.
   */
  acceptSignIn: (result: SignInResult, email: string) => Promise<SignInResult>;
  clearPending: () => void;
````

**`frontend/src/lib/auth.ts`** — edit 3 of 5. Replace:

````ts
  isLoading: true,
  hasHydrated: false,
````

with:

````ts
  isLoading: true,
  hasHydrated: false,
  pending: null,
````

**`frontend/src/lib/auth.ts`** — edit 4 of 5. Replace:

````ts
  login: async (email: string, password: string) => {
    const tokens = await authApi.login({ email, password });
    get().setTokens(tokens);
    const user = await authApi.getMe();
    set({ user });
  },

  signup: async (email: string, password: string, name: string) => {
    const tokens = await authApi.signup({ email, password, name });
    get().setTokens(tokens);
    const user = await authApi.getMe();
    set({ user });
  },
````

with:

````ts
  acceptSignIn: async (result: SignInResult, email: string) => {
    if (result.status === "signed_in") {
      get().setTokens(result);
      const user = await authApi.getMe();
      set({ user, pending: null });
    } else {
      set({
        pending: {
          kind: result.status === "two_step_required" ? "verify" : "setup",
          ticket: result.ticket,
          methods: result.status === "two_step_required" ? result.methods : [],
          passkeyOrigin: result.passkey_origin,
          email,
        },
      });
    }
    return result;
  },

  clearPending: () => set({ pending: null }),

  login: async (email: string, password: string) =>
    get().acceptSignIn(await authApi.login({ email, password }), email),

  signup: async (email: string, password: string, name: string) =>
    get().acceptSignIn(await authApi.signup({ email, password, name }), email),
````

**`frontend/src/lib/auth.ts`** — edit 5 of 5. Replace:

````ts
    setTokenCookie(TOKEN_KEY, null);
    set({ user: null, accessToken: null, refreshToken: null });
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
````

with:

````ts
    setTokenCookie(TOKEN_KEY, null);
    set({ user: null, accessToken: null, refreshToken: null, pending: null });
    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
````

- [ ] **Step 3: Write the passkey helper**

Create `frontend/src/lib/passkeys.ts`:

```ts
/**
 * Passkeys in the browser: the two prompts, and the reasons they may not be
 * usable on this page.
 */

import {
  browserSupportsWebAuthn,
  startAuthentication,
  startRegistration,
  WebAuthnError,
} from "@simplewebauthn/browser";
import type { PasskeyJson } from "./api";

type CreationOptions = Parameters<typeof startRegistration>[0]["optionsJSON"];
type RequestOptions = Parameters<typeof startAuthentication>[0]["optionsJSON"];

/**
 * Why passkeys cannot be used on this page, or null when they can.
 *
 * A passkey is tied to the server's public address (`passkeyOrigin`): opened
 * at any other address, the browser would refuse it. So does a browser
 * without passkey support, or a page that is not on HTTPS or localhost.
 */
export function passkeyUnavailableReason(passkeyOrigin: string): string | null {
  if (typeof window === "undefined") return null;
  if (window.location.origin !== passkeyOrigin) {
    return `Passkeys work only at ${passkeyOrigin}.`;
  }
  if (!window.isSecureContext || !browserSupportsWebAuthn()) {
    return "This browser cannot use passkeys here.";
  }
  return null;
}

/** Ask the browser to create a passkey. Resolves to its answer for the server. */
export async function createPasskey(options: PasskeyJson): Promise<PasskeyJson> {
  const answer = await startRegistration({
    optionsJSON: options as unknown as CreationOptions,
  });
  return answer as unknown as PasskeyJson;
}

/** Ask the browser to sign in with a passkey. Resolves to its answer for the server. */
export async function signInWithPasskey(options: PasskeyJson): Promise<PasskeyJson> {
  const answer = await startAuthentication({
    optionsJSON: options as unknown as RequestOptions,
  });
  return answer as unknown as PasskeyJson;
}

/**
 * True when the browser's own prompt was dismissed, timed out, or was not
 * allowed to open. Nothing was sent, so there is nothing to report: the
 * screen just stays as it is.
 */
export function passkeyPromptDismissed(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  const cause = error instanceof WebAuthnError ? error.cause : undefined;
  const names = [error.name, cause instanceof Error ? cause.name : ""];
  return names.includes("NotAllowedError") || names.includes("AbortError");
}

/** True when the browser says this passkey is already registered to the account. */
export function passkeyAlreadyRegistered(error: unknown): boolean {
  return (
    error instanceof WebAuthnError &&
    error.code === "ERROR_AUTHENTICATOR_PREVIOUSLY_REGISTERED"
  );
}
```

- [ ] **Step 4: Write the two pieces shared with Settings**

Create `frontend/src/components/two-step/authenticator-setup.tsx`:

```tsx
"use client";

import * as React from "react";
import QRCode from "qrcode";
import type { TotpSetup } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** A base32 secret in groups of four, as it is easiest to type. */
function inGroups(secret: string): string {
  return secret.match(/.{1,4}/g)?.join(" ") ?? secret;
}

/**
 * What it takes to add an authenticator app: the QR code to scan, the same
 * secret as text for typing, and a field for the code the app then shows.
 * Used on the sign-in card and in the Settings dialog.
 */
export function AuthenticatorSetup({
  setup,
  code,
  onCodeChange,
  className,
}: {
  setup: TotpSetup;
  code: string;
  onCodeChange: (code: string) => void;
  className?: string;
}) {
  const [qr, setQr] = React.useState<string | null>(null);

  React.useEffect(() => {
    let current = true;
    // Drawn in the browser: the secret is never sent anywhere to be turned
    // into a picture.
    QRCode.toDataURL(setup.otpauth_uri, { margin: 1, width: 176 })
      .then((url) => {
        if (current) setQr(url);
      })
      .catch(() => {
        if (current) setQr(null);
      });
    return () => {
      current = false;
    };
  }, [setup.otpauth_uri]);

  return (
    <div className={cn("space-y-4", className)}>
      <div className="flex justify-center">
        {qr ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={qr}
            alt="QR code to scan with your authenticator app"
            width={176}
            height={176}
            className="rounded-md border bg-white p-1"
          />
        ) : (
          <div className="h-[176px] w-[176px] rounded-md border bg-muted" />
        )}
      </div>

      <div className="text-center text-xs text-muted-foreground">
        Can&apos;t scan? Enter this key in the app:
        <div className="mt-1 select-all break-all font-mono text-sm text-foreground">
          {inGroups(setup.secret)}
        </div>
      </div>

      <div className="space-y-2">
        <label htmlFor="two-step-setup-code" className="text-sm font-medium">
          Code from the app
        </label>
        <Input
          id="two-step-setup-code"
          value={code}
          onChange={(e) => onCodeChange(e.target.value)}
          inputMode="numeric"
          autoComplete="one-time-code"
          placeholder="123456"
          maxLength={12}
          autoFocus
          className="h-10 text-center font-mono text-base tracking-[0.3em]"
        />
      </div>
    </div>
  );
}
```

Create `frontend/src/components/two-step/recovery-codes.tsx`:

```tsx
"use client";

import * as React from "react";
import { toast } from "sonner";
import { Copy, Download } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function asText(codes: string[]): string {
  return [
    "MegooCI recovery codes",
    "Each code works once, in place of a code from your app or a passkey.",
    "",
    ...codes,
    "",
  ].join("\n");
}

/**
 * Recovery codes at the one moment they can be seen, with Copy and Download.
 * Used on the sign-in card and in the Settings dialog.
 */
export function RecoveryCodes({
  codes,
  className,
}: {
  codes: string[];
  className?: string;
}) {
  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      toast.success("Recovery codes copied");
    } catch {
      toast.error("Could not copy. Select the codes and copy them yourself.");
    }
  }

  function download() {
    const url = URL.createObjectURL(new Blob([asText(codes)], { type: "text/plain" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "megooci-recovery-codes.txt";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className={cn("space-y-3", className)}>
      <ul className="grid select-all grid-cols-2 gap-x-6 gap-y-1.5 rounded-lg bg-muted px-4 py-3 font-mono text-sm">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button type="button" variant="outline" className="h-9 flex-1 gap-2" onClick={copy}>
          <Copy className="h-4 w-4" />
          Copy
        </Button>
        <Button type="button" variant="outline" className="h-9 flex-1 gap-2" onClick={download}>
          <Download className="h-4 w-4" />
          Download
        </Button>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write the second step and the forced setup**

Create `frontend/src/components/login/second-step.tsx`:

```tsx
"use client";

import * as React from "react";
import { toast } from "sonner";
import { ArrowLeft, Fingerprint, ShieldCheck } from "lucide-react";
import { errorStatus, twoStepApi, type SignInResult } from "@/lib/api";
import { useAuthStore, type PendingTwoStep } from "@/lib/auth";
import {
  passkeyPromptDismissed,
  passkeyUnavailableReason,
  signInWithPasskey,
} from "@/lib/passkeys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CardContent } from "@/components/ui/card";
import { AuthCard } from "./auth-card";

const linkClass =
  "text-xs text-muted-foreground transition-colors hover:text-primary hover:underline";

/**
 * The second step of signing in, after a correct password: a code from the
 * authenticator app, a passkey, or a recovery code. Shows only what the
 * account has.
 */
export function SecondStep({ pending }: { pending: PendingTwoStep }) {
  const acceptSignIn = useAuthStore((s) => s.acceptSignIn);
  const clearPending = useAuthStore((s) => s.clearPending);
  const [recovering, setRecovering] = React.useState(false);
  const [code, setCode] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const hasCode = pending.methods.includes("totp");
  const hasPasskey = pending.methods.includes("passkey");
  const passkeyProblem = hasPasskey
    ? passkeyUnavailableReason(pending.passkeyOrigin)
    : null;
  const canUsePasskey = hasPasskey && passkeyProblem === null;

  const finish = React.useCallback(
    async (result: SignInResult) => {
      await acceptSignIn(result, pending.email);
      toast.success("Welcome back!");
      if (result.status === "signed_in" && result.recovery_codes_left !== undefined) {
        const left = result.recovery_codes_left;
        toast.warning(
          left === 0
            ? "That was your last recovery code. Generate new ones in Settings."
            : `You have ${left} recovery code${left === 1 ? "" : "s"} left.`,
        );
      }
    },
    [acceptSignIn, pending.email],
  );

  const refuse = React.useCallback(
    (error: unknown, fallback: string) => {
      toast.error(error instanceof Error ? error.message : fallback);
      // The ticket is used up or too old: only the password gets a new one.
      if (errorStatus(error) === 410) clearPending();
    },
    [clearPending],
  );

  async function submitCode(e: React.FormEvent) {
    e.preventDefault();
    const typed = code.trim();
    if (!typed) return;
    setBusy(true);
    try {
      await finish(
        await twoStepApi.verify(
          pending.ticket,
          recovering ? { recovery_code: typed } : { code: typed },
        ),
      );
    } catch (error) {
      refuse(error, "That code is not right.");
    } finally {
      setBusy(false);
    }
  }

  const startPasskey = React.useCallback(async () => {
    setBusy(true);
    try {
      const { options } = await twoStepApi.passkeyOptions(pending.ticket);
      const credential = await signInWithPasskey(options);
      await finish(await twoStepApi.passkeyVerify(pending.ticket, credential));
    } catch (error) {
      // A dismissed prompt sent nothing: the screen just stays as it is.
      if (!passkeyPromptDismissed(error)) refuse(error, "That passkey was not accepted.");
    } finally {
      setBusy(false);
    }
  }, [pending.ticket, finish, refuse]);

  // An account with passkeys only has nothing to type here, so the browser's
  // own prompt opens straight away. Once: the button is there for another try.
  const prompted = React.useRef(false);
  React.useEffect(() => {
    if (prompted.current || hasCode || !canUsePasskey) return;
    prompted.current = true;
    void startPasskey();
  }, [hasCode, canUsePasskey, startPasskey]);

  function switchTo(recovery: boolean) {
    setRecovering(recovery);
    setCode("");
  }

  return (
    <AuthCard
      title={recovering ? "Use a recovery code" : "Two-step verification"}
      description={recovering ? "One of the codes you saved at setup" : pending.email}
    >
      <CardContent className="space-y-4 px-8 pb-8 pt-3">
        {(recovering || hasCode) && (
          <form onSubmit={submitCode} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="second-step-code" className="text-sm font-medium">
                {recovering ? "Recovery code" : "Code from your authenticator app"}
              </label>
              <Input
                id="second-step-code"
                key={recovering ? "recovery" : "code"}
                value={code}
                onChange={(e) => setCode(e.target.value)}
                inputMode={recovering ? "text" : "numeric"}
                autoComplete={recovering ? "off" : "one-time-code"}
                autoCapitalize="none"
                spellCheck={false}
                placeholder={recovering ? "xxxxx-xxxxx" : "123456"}
                maxLength={recovering ? 24 : 12}
                autoFocus
                className={
                  recovering
                    ? "h-10 text-center font-mono"
                    : "h-10 text-center font-mono text-base tracking-[0.3em]"
                }
              />
            </div>
            <Button
              type="submit"
              className="h-10 w-full gap-2 text-sm font-semibold"
              disabled={busy || !code.trim()}
            >
              <ShieldCheck className="h-4 w-4" />
              Verify
            </Button>
            {recovering && (
              <p className="text-xs text-muted-foreground">
                Each code works once. After signing in you are told how many are left.
              </p>
            )}
          </form>
        )}

        {!recovering && hasCode && hasPasskey && (
          <div className="relative flex w-full items-center justify-center py-1">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full border-t" />
            </div>
            <span className="relative bg-card px-3 text-xs text-muted-foreground">or</span>
          </div>
        )}

        {!recovering && hasPasskey && (
          canUsePasskey ? (
            <Button
              type="button"
              variant={hasCode ? "outline" : "default"}
              className="h-10 w-full gap-2 text-sm font-medium"
              onClick={startPasskey}
              disabled={busy}
            >
              <Fingerprint className="h-4 w-4" />
              Use a passkey
            </Button>
          ) : (
            <p className="rounded-md border px-3 py-2 text-xs text-muted-foreground">
              {passkeyProblem}
            </p>
          )
        )}

        <div className="flex flex-col items-center gap-3 pt-1">
          {recovering ? (
            <button type="button" className={linkClass} onClick={() => switchTo(false)}>
              Use a code or passkey instead
            </button>
          ) : (
            <button type="button" className={linkClass} onClick={() => switchTo(true)}>
              Use a recovery code
            </button>
          )}
          <button
            type="button"
            className={`flex items-center gap-1 ${linkClass}`}
            onClick={clearPending}
          >
            <ArrowLeft className="h-3 w-3" />
            Back to sign in
          </button>
        </div>
      </CardContent>
    </AuthCard>
  );
}
```

Create `frontend/src/components/login/two-step-setup.tsx`:

```tsx
"use client";

import * as React from "react";
import { toast } from "sonner";
import { ArrowLeft, Fingerprint, Smartphone } from "lucide-react";
import { errorStatus, twoStepApi, type PasskeyJson, type TotpSetup } from "@/lib/api";
import { useAuthStore, type PendingTwoStep } from "@/lib/auth";
import {
  createPasskey,
  passkeyAlreadyRegistered,
  passkeyPromptDismissed,
  passkeyUnavailableReason,
} from "@/lib/passkeys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CardContent } from "@/components/ui/card";
import { AuthenticatorSetup } from "@/components/two-step/authenticator-setup";
import { RecoveryCodes } from "@/components/two-step/recovery-codes";
import { AuthCard } from "./auth-card";

type Step =
  | { name: "choose" }
  | { name: "authenticator"; setup: TotpSetup }
  | { name: "passkey"; credential: PasskeyJson }
  | { name: "codes"; codes: string[] };

const linkClass =
  "flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-primary hover:underline";
const choiceClass =
  "flex w-full items-start gap-3 rounded-lg border px-3 py-3 text-left transition-colors hover:border-primary hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50";

/**
 * Setting two-step verification up while signing in, on a server that
 * requires it: choose a method, set it up, save the recovery codes. The
 * session only comes at the end.
 */
export function TwoStepSetup({ pending }: { pending: PendingTwoStep }) {
  const acceptSignIn = useAuthStore((s) => s.acceptSignIn);
  const clearPending = useAuthStore((s) => s.clearPending);
  const [step, setStep] = React.useState<Step>({ name: "choose" });
  const [code, setCode] = React.useState("");
  const [passkeyName, setPasskeyName] = React.useState("My passkey");
  const [saved, setSaved] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const passkeyProblem = passkeyUnavailableReason(pending.passkeyOrigin);

  function refuse(error: unknown, fallback: string) {
    toast.error(error instanceof Error ? error.message : fallback);
    // The ticket is too old, or the account was set up elsewhere meanwhile.
    if (errorStatus(error) === 410) clearPending();
  }

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      if (passkeyAlreadyRegistered(error)) {
        toast.error("This passkey is already registered.");
      } else if (!passkeyPromptDismissed(error)) {
        refuse(error, fallback);
      }
    } finally {
      setBusy(false);
    }
  }

  const chooseAuthenticator = () =>
    run(async () => {
      const setup = await twoStepApi.setupTotp(pending.ticket);
      setCode("");
      setStep({ name: "authenticator", setup });
    }, "Could not start the setup.");

  const confirmAuthenticator = (e: React.FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const { recovery_codes } = await twoStepApi.setupTotpConfirm(pending.ticket, code.trim());
      setStep({ name: "codes", codes: recovery_codes ?? [] });
    }, "That code is not right.");
  };

  const choosePasskey = () =>
    run(async () => {
      const { options } = await twoStepApi.setupPasskeyOptions(pending.ticket);
      const credential = await createPasskey(options);
      setStep({ name: "passkey", credential });
    }, "That passkey was not accepted.");

  const savePasskey = (e: React.FormEvent) => {
    e.preventDefault();
    if (step.name !== "passkey") return;
    const credential = step.credential;
    return run(async () => {
      const { recovery_codes } = await twoStepApi.setupPasskey(
        pending.ticket,
        credential,
        passkeyName.trim(),
      );
      setStep({ name: "codes", codes: recovery_codes ?? [] });
    }, "That passkey was not accepted.");
  };

  const finish = () =>
    run(async () => {
      await acceptSignIn(await twoStepApi.setupFinish(pending.ticket), pending.email);
      toast.success("Two-step verification is on. Welcome to MegooCI!");
    }, "Could not finish signing in.");

  const backToSignIn = (
    <button type="button" className={linkClass} onClick={clearPending}>
      <ArrowLeft className="h-3 w-3" />
      Back to sign in
    </button>
  );

  if (step.name === "authenticator") {
    return (
      <AuthCard title="Scan with your app" description="Then enter the code it shows">
        <CardContent className="px-8 pb-8 pt-3">
          <form onSubmit={confirmAuthenticator} className="space-y-4">
            <AuthenticatorSetup setup={step.setup} code={code} onCodeChange={setCode} />
            <Button
              type="submit"
              className="h-10 w-full text-sm font-semibold"
              disabled={busy || !code.trim()}
            >
              Confirm
            </Button>
            <div className="flex justify-center">
              <button
                type="button"
                className={linkClass}
                onClick={() => setStep({ name: "choose" })}
              >
                <ArrowLeft className="h-3 w-3" />
                Choose another method
              </button>
            </div>
          </form>
        </CardContent>
      </AuthCard>
    );
  }

  if (step.name === "passkey") {
    return (
      <AuthCard
        title="Name this passkey"
        description="Your browser has created it. Give it a name you will recognise."
      >
        <CardContent className="px-8 pb-8 pt-3">
          <form onSubmit={savePasskey} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="setup-passkey-name" className="text-sm font-medium">
                Name
              </label>
              <Input
                id="setup-passkey-name"
                value={passkeyName}
                onChange={(e) => setPasskeyName(e.target.value)}
                maxLength={100}
                autoFocus
                className="h-10"
              />
            </div>
            <Button type="submit" className="h-10 w-full text-sm font-semibold" disabled={busy}>
              Save
            </Button>
          </form>
        </CardContent>
      </AuthCard>
    );
  }

  if (step.name === "codes") {
    return (
      <AuthCard title="Save your recovery codes" description="They are shown only once">
        <CardContent className="space-y-4 px-8 pb-8 pt-3">
          <p className="text-xs text-muted-foreground">
            Each code works once, in place of a code from your app or a passkey, for
            when you have neither.
          </p>
          <RecoveryCodes codes={step.codes} />
          <label className="flex cursor-pointer items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={saved}
              onChange={(e) => setSaved(e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            I have saved these codes
          </label>
          <Button
            type="button"
            className="h-10 w-full text-sm font-semibold"
            onClick={finish}
            disabled={busy || !saved}
          >
            Continue to MegooCI
          </Button>
        </CardContent>
      </AuthCard>
    );
  }

  return (
    <AuthCard
      title="Set up two-step verification"
      description="Your administrator requires it for every account"
    >
      <CardContent className="space-y-3 px-8 pb-8 pt-3">
        <button type="button" className={choiceClass} onClick={chooseAuthenticator} disabled={busy}>
          <Smartphone className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <span>
            <span className="block text-sm font-medium">Authenticator app</span>
            <span className="block text-xs text-muted-foreground">
              Codes from an app such as Google Authenticator, 1Password or Authy
            </span>
          </span>
        </button>
        <button
          type="button"
          className={choiceClass}
          onClick={choosePasskey}
          disabled={busy || passkeyProblem !== null}
        >
          <Fingerprint className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
          <span>
            <span className="block text-sm font-medium">Passkey</span>
            <span className="block text-xs text-muted-foreground">
              {passkeyProblem ?? "Fingerprint, face, device PIN or a security key"}
            </span>
          </span>
        </button>
        <div className="flex justify-center pt-1">{backToSignIn}</div>
      </CardContent>
    </AuthCard>
  );
}
```

- [ ] **Step 6: Hand over to them from the sign-in, sign-up and invitation pages**

**`frontend/src/components/login/sign-in-form.tsx`**. Replace:

````tsx
      await login(email, password);
      toast.success("Welcome back!");
      router.push(redirectTo);
````

with:

````tsx
      const result = await login(email, password);
      // With two-step verification the password is not the end: the store
      // now holds a pending sign-in, and the page shows its next step.
      if (result.status === "signed_in") {
        toast.success("Welcome back!");
        router.push(redirectTo);
      }
````

**`frontend/src/app/login/page.tsx`** — edit 1 of 3. Replace:

````tsx
import { AuthBackdrop } from "@/components/login/auth-backdrop";
import { SignInForm } from "@/components/login/sign-in-form";
````

with:

````tsx
import { AuthBackdrop } from "@/components/login/auth-backdrop";
import { SecondStep } from "@/components/login/second-step";
import { SignInForm } from "@/components/login/sign-in-form";
import { TwoStepSetup } from "@/components/login/two-step-setup";
````

**`frontend/src/app/login/page.tsx`** — edit 2 of 3. Replace:

````tsx
  const { accessToken, hasHydrated } = useAuthStore();
````

with:

````tsx
  const { accessToken, hasHydrated, pending } = useAuthStore();
````

**`frontend/src/app/login/page.tsx`** — edit 3 of 3. Replace:

````tsx
  if (accounts === null) return null;

  if (chosen) {
````

with:

````tsx
  if (accounts === null) return null;

  // A correct password that is waiting for its second step, or for two-step
  // verification to be set up, here or on the sign-up or invitation page.
  if (pending) {
    return pending.kind === "verify" ? (
      <SecondStep key={pending.ticket} pending={pending} />
    ) : (
      <TwoStepSetup key={pending.ticket} pending={pending} />
    );
  }

  if (chosen) {
````

**`frontend/src/app/signup/page.tsx`**. Replace:

````tsx
      await signup(email, password, fullName);
      toast.success("Account created! Welcome to MegooCI.");
      router.push("/dashboard");
````

with:

````tsx
      const result = await signup(email, password, fullName);
      if (result.status === "signed_in") {
        toast.success("Account created! Welcome to MegooCI.");
        router.push("/dashboard");
      } else {
        // This server requires two-step verification: the sign-in page
        // takes the new account through setting it up.
        toast.success("Account created. One more step: set up two-step verification.");
        router.push("/login");
      }
````

**`frontend/src/app/invite/accept/page.tsx`** — edit 1 of 2. Replace:

````tsx
  const { setTokens, loadUser } = useAuthStore();
````

with:

````tsx
  const acceptSignIn = useAuthStore((s) => s.acceptSignIn);
````

**`frontend/src/app/invite/accept/page.tsx`** — edit 2 of 2. Replace:

````tsx
      const tokens = await invitesApi.accept({ token, name, password });
      setTokens(tokens);
      await loadUser();
      toast.success("Welcome to MegooCI!");
      router.push("/dashboard");
````

with:

````tsx
      const result = await acceptSignIn(
        await invitesApi.accept({ token, name, password }),
        "",
      );
      if (result.status === "signed_in") {
        toast.success("Welcome to MegooCI!");
        router.push("/dashboard");
      } else {
        // This server requires two-step verification: the sign-in page
        // takes the new account through setting it up.
        toast.success("Account created. One more step: set up two-step verification.");
        router.push("/login");
      }
````

- [ ] **Step 7: Type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 8: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/api.ts frontend/src/lib/auth.ts frontend/src/lib/passkeys.ts frontend/src/components/two-step frontend/src/components/login frontend/src/app/login/page.tsx frontend/src/app/signup/page.tsx frontend/src/app/invite/accept/page.tsx
git commit -m "feat(ui): the second step and the forced setup on the sign-in page

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: Frontend — the settings tabs

**Files:**
- Create: `frontend/src/components/two-step/password-dialog.tsx`, `frontend/src/components/settings/two-step-tab.tsx`, `frontend/src/components/settings/security-tab.tsx`
- Modify: `frontend/src/app/settings/page.tsx`

**Interfaces:**
- Consumes: `twoStepApi`, `systemApi.security`, `systemApi.setSecurity`, `authApi.getMe` (Task 13); `AuthenticatorSetup`, `RecoveryCodes`; the helpers of `lib/passkeys.ts`; `useConfirm`.
- Produces: `PasswordDialog({ prompt, onClose })` with `PasswordPrompt` (`title`, `description`, `confirmLabel`, `destructive?`, `action(password)`), `TwoStepTab()`, `SecurityTab()`.
- Produces: the tabs `two-step` (Account, after Password) and `security` (Server, after Maintenance, administrators only), reachable as `/settings?tab=two-step` and `/settings?tab=security`.

- [ ] **Step 1: Write the password prompt**

Create `frontend/src/components/two-step/password-dialog.tsx`:

```tsx
"use client";

import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export interface PasswordPrompt {
  title: string;
  description: string;
  confirmLabel: string;
  destructive?: boolean;
  /** Called with the typed password. Throw to keep the dialog open. */
  action: (password: string) => Promise<void>;
}

/**
 * Asks for the account's password before something that weakens how the
 * account signs in: removing a method, or replacing the recovery codes.
 */
export function PasswordDialog({
  prompt,
  onClose,
}: {
  prompt: PasswordPrompt | null;
  onClose: () => void;
}) {
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    setPassword("");
  }, [prompt]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt || !password) return;
    setBusy(true);
    try {
      await prompt.action(password);
      onClose();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={prompt !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        {prompt && (
          <form onSubmit={submit}>
            <DialogHeader>
              <DialogTitle>{prompt.title}</DialogTitle>
              <DialogDescription>{prompt.description}</DialogDescription>
            </DialogHeader>
            <div className="space-y-2 py-4">
              <label htmlFor="two-step-password" className="text-sm font-medium">
                Your password
              </label>
              <Input
                id="two-step-password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                autoFocus
              />
            </div>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={onClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant={prompt.destructive ? "destructive" : "default"}
                disabled={busy || !password}
              >
                {prompt.confirmLabel}
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 2: Write the two tabs**

Create `frontend/src/components/settings/two-step-tab.tsx`:

```tsx
"use client";

import * as React from "react";
import { toast } from "sonner";
import { format } from "date-fns";
import { Fingerprint, LifeBuoy, ShieldCheck, Smartphone } from "lucide-react";
import {
  authApi,
  twoStepApi,
  type PasskeyInfo,
  type PasskeyJson,
  type TotpSetup,
  type TwoStepSummary,
} from "@/lib/api";
import { useAuthStore } from "@/lib/auth";
import {
  createPasskey,
  passkeyAlreadyRegistered,
  passkeyPromptDismissed,
  passkeyUnavailableReason,
} from "@/lib/passkeys";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { AuthenticatorSetup } from "@/components/two-step/authenticator-setup";
import { PasswordDialog, type PasswordPrompt } from "@/components/two-step/password-dialog";
import { RecoveryCodes } from "@/components/two-step/recovery-codes";

type Open =
  | { kind: "authenticator"; setup: TotpSetup }
  | { kind: "passkey-name"; ticket: string; credential: PasskeyJson }
  | { kind: "rename"; passkey: PasskeyInfo }
  | { kind: "codes"; codes: string[] }
  | null;

function day(value: string | null): string {
  return value ? format(new Date(value), "d MMM yyyy") : "never";
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

/** Settings → Account → Two-step verification. */
export function TwoStepTab() {
  const [summary, setSummary] = React.useState<TwoStepSummary | null>(null);
  const [open, setOpen] = React.useState<Open>(null);
  const [prompt, setPrompt] = React.useState<PasswordPrompt | null>(null);
  const [code, setCode] = React.useState("");
  const [name, setName] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const load = React.useCallback(async () => {
    try {
      setSummary(await twoStepApi.summary());
    } catch (error) {
      toast.error(message(error, "Failed to load two-step verification"));
    }
  }, []);

  React.useEffect(() => {
    void load();
  }, [load]);

  /** After a change: the cards, and what the rest of the app knows (the banner). */
  const changed = React.useCallback(
    async (next?: TwoStepSummary) => {
      if (next) setSummary(next);
      else await load();
      try {
        useAuthStore.setState({ user: await authApi.getMe() });
      } catch {
        // The banner catches up on the next page load.
      }
    },
    [load],
  );

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      if (passkeyAlreadyRegistered(error)) {
        toast.error("This passkey is already registered.");
      } else if (!passkeyPromptDismissed(error)) {
        toast.error(message(error, fallback));
      }
    } finally {
      setBusy(false);
    }
  }

  /** New recovery codes are shown at once: it is the only time they can be. */
  function showCodesOr(codes: string[] | null, done: string) {
    if (codes && codes.length > 0) setOpen({ kind: "codes", codes });
    else {
      setOpen(null);
      toast.success(done);
    }
  }

  // ── authenticator app ───────────────────────────────────────────────

  const setUpAuthenticator = () =>
    run(async () => {
      const setup = await twoStepApi.beginTotp();
      setCode("");
      setOpen({ kind: "authenticator", setup });
    }, "Could not start the setup.");

  const confirmAuthenticator = (e: React.FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const { recovery_codes } = await twoStepApi.confirmTotp(code.trim());
      await changed();
      showCodesOr(recovery_codes, "Authenticator app added");
    }, "That code is not right.");
  };

  function removeAuthenticator() {
    if (!summary) return;
    const passkeys = summary.passkeys.length;
    setPrompt({
      title: "Remove the authenticator app?",
      description:
        passkeys > 0
          ? `You still have ${passkeys} passkey${passkeys === 1 ? "" : "s"}, so two-step verification stays on.`
          : "This turns two-step verification off and deletes your recovery codes.",
      confirmLabel: "Remove",
      destructive: true,
      action: async (password) => {
        await changed(await twoStepApi.removeTotp(password));
        toast.success("Authenticator app removed");
      },
    });
  }

  // ── passkeys ────────────────────────────────────────────────────────

  const addPasskey = () =>
    run(async () => {
      const { options, ticket } = await twoStepApi.passkeyRegistrationOptions();
      const credential = await createPasskey(options);
      setName("My passkey");
      setOpen({ kind: "passkey-name", ticket, credential });
    }, "That passkey was not accepted.");

  const savePasskey = (e: React.FormEvent) => {
    e.preventDefault();
    if (open?.kind !== "passkey-name") return;
    const { ticket, credential } = open;
    return run(async () => {
      const { recovery_codes } = await twoStepApi.addPasskey(ticket, credential, name.trim());
      await changed();
      showCodesOr(recovery_codes, "Passkey added");
    }, "That passkey was not accepted.");
  };

  const renamePasskey = (e: React.FormEvent) => {
    e.preventDefault();
    if (open?.kind !== "rename") return;
    const { passkey } = open;
    return run(async () => {
      await twoStepApi.renamePasskey(passkey.id, name.trim());
      await load();
      setOpen(null);
    }, "Could not rename the passkey.");
  };

  function removePasskey(passkey: PasskeyInfo) {
    if (!summary) return;
    const others = summary.passkeys.length - 1 + (summary.totp ? 1 : 0);
    setPrompt({
      title: `Remove "${passkey.name}"?`,
      description:
        others > 0
          ? "You have another method, so two-step verification stays on."
          : "This turns two-step verification off and deletes your recovery codes.",
      confirmLabel: "Remove",
      destructive: true,
      action: async (password) => {
        await changed(await twoStepApi.removePasskey(passkey.id, password));
        toast.success("Passkey removed");
      },
    });
  }

  // ── recovery codes ──────────────────────────────────────────────────

  function regenerateCodes() {
    setPrompt({
      title: "Generate new recovery codes?",
      description: "The codes you have now stop working. Save the new ones somewhere safe.",
      confirmLabel: "Generate",
      action: async (password) => {
        const { recovery_codes } = await twoStepApi.regenerateRecoveryCodes(password);
        await load();
        setOpen({ kind: "codes", codes: recovery_codes ?? [] });
      },
    });
  }

  if (!summary) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  const passkeyProblem = passkeyUnavailableReason(summary.passkey_origin);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="h-5 w-5" />
                Two-step verification
              </CardTitle>
              <CardDescription>
                After your password, sign-in asks for a code or a passkey.
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {summary.required && (
                <Badge variant="warning">Required by your administrator</Badge>
              )}
              <Badge variant={summary.enabled ? "success" : "pending"}>
                {summary.enabled ? "On" : "Off"}
              </Badge>
            </div>
          </div>
        </CardHeader>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Smartphone className="h-4 w-4" />
            Authenticator app
          </CardTitle>
          <CardDescription>A 6-digit code from an app on your phone.</CardDescription>
        </CardHeader>
        <CardContent>
          {summary.totp ? (
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm">
                Set up
                <p className="text-xs text-muted-foreground">
                  Added {day(summary.totp.confirmed_at)} · last used{" "}
                  {day(summary.totp.last_used_at)}
                </p>
              </div>
              <Button
                variant="outline"
                size="sm"
                className="text-destructive"
                onClick={removeAuthenticator}
                disabled={busy}
              >
                Remove
              </Button>
            </div>
          ) : (
            <Button size="sm" onClick={setUpAuthenticator} disabled={busy}>
              Set up
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Fingerprint className="h-4 w-4" />
            Passkeys
          </CardTitle>
          <CardDescription>
            Fingerprint, face, device PIN or a security key. You can add several.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {summary.passkeys.map((passkey) => (
            <div
              key={passkey.id}
              className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2.5"
            >
              <div className="min-w-0 text-sm">
                <p className="truncate font-medium">{passkey.name}</p>
                <p className="text-xs text-muted-foreground">
                  Added {day(passkey.created_at)} · last used {day(passkey.last_used_at)}
                </p>
              </div>
              <div className="flex shrink-0 gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setName(passkey.name);
                    setOpen({ kind: "rename", passkey });
                  }}
                  disabled={busy}
                >
                  Rename
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  className="text-destructive"
                  onClick={() => removePasskey(passkey)}
                  disabled={busy}
                >
                  Remove
                </Button>
              </div>
            </div>
          ))}
          {passkeyProblem ? (
            <p className="text-xs text-muted-foreground">{passkeyProblem}</p>
          ) : (
            <Button size="sm" onClick={addPasskey} disabled={busy}>
              Add a passkey
            </Button>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <LifeBuoy className="h-4 w-4" />
            Recovery codes
          </CardTitle>
          <CardDescription>
            For when you have neither your phone nor a passkey. Each works once.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {summary.recovery_codes ? (
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm">
                {summary.recovery_codes.left} of 10 left
                <p className="text-xs text-muted-foreground">
                  Generated {day(summary.recovery_codes.generated_at)}
                </p>
              </div>
              <Button variant="outline" size="sm" onClick={regenerateCodes} disabled={busy}>
                Generate new codes
              </Button>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              You get ten codes when you add your first method.
            </p>
          )}
        </CardContent>
      </Card>

      <PasswordDialog prompt={prompt} onClose={() => setPrompt(null)} />

      <Dialog
        open={open !== null}
        onOpenChange={(next) => {
          // Recovery codes are closed with their own button, once saved.
          if (!next && open?.kind !== "codes") setOpen(null);
        }}
      >
        <DialogContent>
          {open?.kind === "authenticator" && (
            <form onSubmit={confirmAuthenticator}>
              <DialogHeader>
                <DialogTitle>Set up an authenticator app</DialogTitle>
                <DialogDescription>
                  Scan the code with your app, then enter the 6 digits it shows.
                </DialogDescription>
              </DialogHeader>
              <AuthenticatorSetup
                setup={open.setup}
                code={code}
                onCodeChange={setCode}
                className="py-4"
              />
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setOpen(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || !code.trim()}>
                  Confirm
                </Button>
              </DialogFooter>
            </form>
          )}

          {(open?.kind === "passkey-name" || open?.kind === "rename") && (
            <form onSubmit={open.kind === "rename" ? renamePasskey : savePasskey}>
              <DialogHeader>
                <DialogTitle>
                  {open.kind === "rename" ? "Rename passkey" : "Name this passkey"}
                </DialogTitle>
                <DialogDescription>
                  {open.kind === "rename"
                    ? "A name you will recognise it by."
                    : "Your browser has created the passkey. Give it a name you will recognise."}
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-2 py-4">
                <label htmlFor="passkey-name" className="text-sm font-medium">
                  Name
                </label>
                <Input
                  id="passkey-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  maxLength={100}
                  autoFocus
                />
              </div>
              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => setOpen(null)}>
                  Cancel
                </Button>
                <Button type="submit" disabled={busy || !name.trim()}>
                  Save
                </Button>
              </DialogFooter>
            </form>
          )}

          {open?.kind === "codes" && (
            <>
              <DialogHeader>
                <DialogTitle>Save your recovery codes</DialogTitle>
                <DialogDescription>
                  They are shown only once. Each works once, in place of a code or a
                  passkey.
                </DialogDescription>
              </DialogHeader>
              <RecoveryCodes codes={open.codes} className="py-4" />
              <DialogFooter>
                <Button type="button" onClick={() => setOpen(null)}>
                  I have saved them
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

Create `frontend/src/components/settings/security-tab.tsx`:

```tsx
"use client";

import * as React from "react";
import Link from "next/link";
import { toast } from "sonner";
import { ShieldCheck } from "lucide-react";
import { systemApi, type SecuritySettings } from "@/lib/api";
import { useConfirm } from "@/components/ui/confirm-dialog";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

/** Settings → Server → Security, for administrators. */
export function SecurityTab() {
  const [settings, setSettings] = React.useState<SecuritySettings | null>(null);
  const [saving, setSaving] = React.useState(false);
  const confirm = useConfirm();

  React.useEffect(() => {
    systemApi
      .security()
      .then(setSettings)
      .catch((error: unknown) =>
        toast.error(error instanceof Error ? error.message : "Failed to load security settings"),
      );
  }, []);

  async function toggle(required: boolean) {
    if (!settings) return;
    const without = settings.users_total - settings.users_with_two_step;
    if (required) {
      const ok = await confirm({
        title: "Require two-step verification?",
        description:
          without > 0
            ? `${without} of ${settings.users_total} users have not set it up. They see a notice now, and within 12 hours they are signed out and set it up when they sign in again.`
            : "Every user already has it on. New accounts will set it up before their first sign-in.",
        confirmText: "Require",
        tone: "warning",
      });
      if (!ok) return;
    }
    setSaving(true);
    try {
      setSettings(await systemApi.setSecurity(required));
      toast.success(
        required
          ? "Two-step verification is now required"
          : "Two-step verification is no longer required",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  }

  if (!settings) return <Skeleton className="h-40 w-full" />;

  const required = settings.require_two_step;
  const without = settings.users_total - settings.users_with_two_step;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ShieldCheck className="h-5 w-5" />
          Security
        </CardTitle>
        <CardDescription>How accounts on this server sign in.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between gap-4">
          <div className="text-sm">
            <p className="font-medium">Require two-step verification</p>
            <p className="text-xs text-muted-foreground">
              Everyone must set it up: new accounts before their first sign-in, existing
              ones within 12 hours. You need it on your own account first.
            </p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={required}
            aria-label="Require two-step verification"
            disabled={saving}
            onClick={() => toggle(!required)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
              required ? "bg-primary" : "bg-muted"
            }`}
          >
            <span
              className={`pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0 transition-transform ${
                required ? "translate-x-5" : "translate-x-0"
              }`}
            />
          </button>
        </div>

        <div className="flex items-center justify-between gap-4 border-t pt-4">
          <div className="text-sm">
            <p className="font-medium">
              {settings.users_with_two_step} of {settings.users_total} users have it on
            </p>
            <p className="text-xs text-muted-foreground">
              {without === 0
                ? "Nobody is left to set it up."
                : required
                  ? `The other ${without} will be asked to set it up.`
                  : `The other ${without} sign in with a password only.`}
            </p>
          </div>
          <Link href="/admin/users">
            <Button variant="outline" size="sm">
              View users
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 3: Add them to the settings page**

**`frontend/src/app/settings/page.tsx`** — edit 1 of 8. Replace:

````tsx
import { KeyRound, Lock, Monitor, Palette, Sparkles, User, Wrench } from "lucide-react";
````

with:

````tsx
import {
  KeyRound,
  Lock,
  Monitor,
  Palette,
  Shield,
  ShieldCheck,
  Sparkles,
  User,
  Wrench,
} from "lucide-react";
````

**`frontend/src/app/settings/page.tsx`** — edit 2 of 8. Replace:

````tsx
import { PasswordTab } from "@/components/settings/password-tab";
````

with:

````tsx
import { PasswordTab } from "@/components/settings/password-tab";
import { TwoStepTab } from "@/components/settings/two-step-tab";
````

**`frontend/src/app/settings/page.tsx`** — edit 3 of 8. Replace:

````tsx
import { MaintenanceCard } from "@/components/settings/maintenance-tab";
````

with:

````tsx
import { MaintenanceCard } from "@/components/settings/maintenance-tab";
import { SecurityTab } from "@/components/settings/security-tab";
````

**`frontend/src/app/settings/page.tsx`** — edit 4 of 8. Replace:

````tsx
  | "password"
  | "appearance"
  | "api-tokens"
  | "maintenance"
````

with:

````tsx
  | "password"
  | "two-step"
  | "appearance"
  | "api-tokens"
  | "maintenance"
  | "security"
````

**`frontend/src/app/settings/page.tsx`** — edit 5 of 8. Replace:

````tsx
            : []),
          { id: "appearance", label: "Appearance", icon: Palette },
````

with:

````tsx
            : []),
          { id: "two-step", label: "Two-step verification", icon: ShieldCheck },
          { id: "appearance", label: "Appearance", icon: Palette },
````

**`frontend/src/app/settings/page.tsx`** — edit 6 of 8. Replace:

````tsx
          ...(isAdmin
            ? [{ id: "maintenance" as const, label: "Maintenance", icon: Wrench }]
            : []),
````

with:

````tsx
          ...(isAdmin
            ? [
                { id: "maintenance" as const, label: "Maintenance", icon: Wrench },
                { id: "security" as const, label: "Security", icon: Shield },
              ]
            : []),
````

**`frontend/src/app/settings/page.tsx`** — edit 7 of 8. Replace:

````tsx
    password: () => <PasswordTab />,
````

with:

````tsx
    password: () => <PasswordTab />,
    "two-step": () => <TwoStepTab />,
````

**`frontend/src/app/settings/page.tsx`** — edit 8 of 8. Replace:

````tsx
    "ai-assistant": () => (
````

with:

````tsx
    security: () => <SecurityTab />,
    "ai-assistant": () => (
````

- [ ] **Step 4: Type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/two-step/password-dialog.tsx frontend/src/components/settings/two-step-tab.tsx frontend/src/components/settings/security-tab.tsx frontend/src/app/settings/page.tsx
git commit -m "feat(ui): settings tabs for two-step verification and the requirement

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Frontend — the users page and the notice

**Files:**
- Modify: `frontend/src/app/admin/users/page.tsx`, `frontend/src/components/layout/app-layout.tsx`

**Interfaces:**
- Consumes: `usersApi.resetTwoStep`, `UserDetail.two_step_enabled`, `User.two_step_setup_required` (Task 13).
- Produces: a "Two-step on" / "Two-step off" badge beside each user's name (the page is a list of rows, not a table, so the design's column is a badge), "Reset two-step verification" in each row's menu (off for the signed-in administrator's own row and for users who have none), and a notice on every page while the signed-in account owes a setup.

- [ ] **Step 1: Add the badge, the reset and the notice**

**`frontend/src/app/admin/users/page.tsx`** — edit 1 of 4. Replace:

````tsx
  Send,
  Trash2,
} from "lucide-react";
````

with:

````tsx
  Send,
  ShieldOff,
  Trash2,
} from "lucide-react";
````

**`frontend/src/app/admin/users/page.tsx`** — edit 2 of 4. Replace:

````tsx
  const handleDeleteUser = async (u: UserDetail) => {
````

with:

````tsx
  const handleResetTwoStep = async (u: UserDetail) => {
    const ok = await confirm({
      title: "Reset two-step verification?",
      description: `This removes the authenticator app, passkeys and recovery codes of ${u.name} (${u.email}). They sign in with their password alone until they set it up again; where it is required, they are asked to at their next sign-in.`,
      confirmText: "Reset",
      tone: "warning",
    });
    if (!ok) return;
    try {
      await usersApi.resetTwoStep(u.id);
      toast.success(`Two-step verification reset for ${u.name}`);
      loadData();
    } catch (e: unknown) {
      const msg =
        (e as { body?: { detail?: string } })?.body?.detail ||
        "Failed to reset two-step verification";
      toast.error(msg);
    }
  };

  const handleDeleteUser = async (u: UserDetail) => {
````

**`frontend/src/app/admin/users/page.tsx`** — edit 3 of 4. Replace:

````tsx
                        {!u.is_active && (
                          <Badge variant="failed" className="text-[10px]">Inactive</Badge>
                        )}
````

with:

````tsx
                        {!u.is_active && (
                          <Badge variant="failed" className="text-[10px]">Inactive</Badge>
                        )}
                        <Badge
                          variant={u.two_step_enabled ? "success" : "pending"}
                          className="text-[10px]"
                          title="Two-step verification"
                        >
                          {u.two_step_enabled ? "Two-step on" : "Two-step off"}
                        </Badge>
````

**`frontend/src/app/admin/users/page.tsx`** — edit 4 of 4. Replace:

````tsx
                        <DropdownMenuItem
                          onClick={() => handleDeleteUser(u)}
````

with:

````tsx
                        <DropdownMenuItem
                          onClick={() => handleResetTwoStep(u)}
                          disabled={!u.two_step_enabled || u.id === currentUser?.id}
                        >
                          <ShieldOff className="mr-2 h-4 w-4" /> Reset two-step verification
                        </DropdownMenuItem>
                        <DropdownMenuItem
                          onClick={() => handleDeleteUser(u)}
````

**`frontend/src/components/layout/app-layout.tsx`** — edit 1 of 3. Replace:

````tsx
import { AlertTriangle } from "lucide-react";
````

with:

````tsx
import { AlertTriangle, ShieldAlert } from "lucide-react";
````

**`frontend/src/components/layout/app-layout.tsx`** — edit 2 of 3. Replace:

````tsx
  const { accessToken, isLoading, loadUser } = useAuthStore();
````

with:

````tsx
  const { accessToken, isLoading, loadUser, user } = useAuthStore();
````

**`frontend/src/components/layout/app-layout.tsx`** — edit 3 of 3. Replace:

````tsx
          <main className="flex-1 overflow-auto bg-background p-4 sm:p-6">
````

with:

````tsx
          {user?.two_step_setup_required && (
            <div className="flex items-center gap-2 border-b border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-700 dark:text-amber-300">
              <ShieldAlert className="h-4 w-4 shrink-0" />
              <span className="font-medium">
                Your administrator requires two-step verification.
              </span>
              <span className="hidden text-amber-600 dark:text-amber-400 sm:inline">
                Set it up now, or you will be signed out and asked to.
              </span>
              {/* A plain link: the settings page reads its tab from the
                  address when it loads, also when it is already open. */}
              {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
              <a
                href="/settings?tab=two-step"
                className="ml-auto shrink-0 font-medium underline underline-offset-2"
              >
                Set it up
              </a>
            </div>
          )}
          <main className="flex-1 overflow-auto bg-background p-4 sm:p-6">
````

- [ ] **Step 2: Type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 3: Build**

Run from `frontend/`:

```bash
npx next build
```

Expected: `Compiled successfully`, and `/login`, `/signup`, `/settings`, `/admin/users` and `/invite/accept` in the list of routes. The build rewrites `next-env.d.ts`; put it back with `git checkout -- frontend/next-env.d.ts` from the repository root.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/app/admin/users/page.tsx frontend/src/components/layout/app-layout.tsx
git commit -m "feat(ui): who has two-step verification, a reset for administrators, and a notice when it is owed

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Documentation and the last checks

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document it**

**`README.md`** — edit 1 of 2. Replace:

````markdown
- **Long-lived sessions** — 12-hour access tokens with silent refresh up to 30 days.
````

with:

````markdown
- **Long-lived sessions** — 12-hour access tokens with silent refresh up to 30 days.
- **Two-step verification** — after the password, a code from an authenticator app or a passkey. Recovery codes for a lost device, and a switch for administrators to require it for every account.
````

**`README.md`** — edit 2 of 2. Replace:

````markdown
## Running a Self-Hosted Build Agent
````

with:

````markdown
## Two-Step Verification

Signing in always starts with the password. An account with two-step verification then proves itself a second time, with a 6-digit code from an authenticator app or with a passkey.

- **Turning it on.** Under **Settings → Two-step verification**, set up an authenticator app (Google Authenticator, 1Password, Authy, …), add one or more passkeys, or both. Removing a method asks for your password.
- **Recovery codes.** Adding your first method gives you ten one-time codes, shown once. Any of them works in place of the second step when you have neither your phone nor a passkey. Generate new ones in Settings at any time; the old ones stop working.
- **Passkeys and the server address.** A passkey works only at the address in `MEGOOCI_PUBLIC_URL`, over HTTPS or on `localhost`. Opened anywhere else, the app hides the passkey option and says where it works. If that address ever changes, existing passkeys stop working; authenticator and recovery codes do not.
- **Requiring it.** Administrators turn on **Settings → Security → Require two-step verification**, after setting it up on their own account. New accounts then set it up before their first sign-in. People already signed in see a notice, and within 12 hours their session ends and the sign-in page takes them through the setup.
- **Someone lost everything.** On the **Users** page, **Reset two-step verification** removes a user's authenticator app, passkeys and recovery codes; they sign in with their password and set it up again. For an administrator with nobody left to reset them, run this where the backend runs:

  ```bash
  docker compose exec backend python -m app.reset_two_step admin@example.com
  ```

- **`docker login`.** The registry cannot ask for a second step, so an account that uses two-step verification (or must set it up) cannot log in to it with its password. Create an API token under **Settings → API tokens**, with the **Container registry** scope for pulling and pushing, and use it as the password. Deploy tokens are unaffected.
- **API tokens and MCP** work as before, without a second step. A token cannot change how its owner signs in.
- **Backups** include two-step verification. Restoring one never changes it for the administrator who runs the restore.

## Running a Self-Hosted Build Agent
````

- [ ] **Step 2: Run the whole backend suite**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `1249 passed`.

- [ ] **Step 3: Type-check the frontend once more**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(two-step): document two-step verification

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Hand over what only a person can try**

Nothing in this plan opens a browser, holds a phone or runs a docker client against the server. Report these as not run, for the person to try on a rebuilt stack before relying on the feature:

1. Set up an authenticator app from Settings with a real phone, sign out, and sign in with a code; then with a recovery code.
2. Add a passkey (fingerprint, device PIN or a security key) and sign in with it; cancel the browser's prompt once and see that nothing breaks.
3. Open the app at an address other than `MEGOOCI_PUBLIC_URL` and see the passkey controls replaced by the line that says where passkeys work.
4. Turn the requirement on as an administrator; in another browser, see the notice as a member, then sign in afresh as a new account and go through the forced setup.
5. Reset a user from the Users page; run `python -m app.reset_two_step <email>` in the backend container.
6. `docker login` with the password of an account that uses two-step verification (expect the refusal with its instruction), then with an API token that has the Container registry scope.
7. Ask for a password-reset email and use its link: the password changes, and the link's token does not sign anyone in.
8. Make a backup, change an account's two-step verification, restore, and check both that account and the restoring administrator.
