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
