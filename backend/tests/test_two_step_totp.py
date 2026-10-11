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
