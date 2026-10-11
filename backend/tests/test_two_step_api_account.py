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
