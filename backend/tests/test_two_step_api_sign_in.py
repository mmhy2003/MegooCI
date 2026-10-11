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
