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
