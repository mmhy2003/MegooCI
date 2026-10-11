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
