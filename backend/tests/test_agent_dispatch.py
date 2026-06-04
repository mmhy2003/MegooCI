"""Regression tests for build dispatch / agent reservation handling.

These cover the failure mode where a genuinely idle, online agent is left with
a stale ``current_build_id`` reservation, so the scheduler keeps skipping it and
pending builds pile up even though there is free capacity.

The production models use Postgres-only column types (``UUID``, ``JSONB``,
``ARRAY``). We register lightweight SQLite renderings for them so the *real*
dispatcher / executor code can run against an in-memory database — the
``@compiles`` hooks only affect the ``sqlite`` dialect and never touch
production Postgres behaviour.
"""

import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# execute_build builds a redis client from settings; give it a value so import
# and lazy client construction never blow up. No real connection is made on the
# code paths under test (the client is closed without ever being used).
os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@pytest_asyncio.fixture
async def session_factory():
    """A function-scoped in-memory SQLite session factory with the ``agents``
    and ``builds`` tables created. StaticPool keeps a single underlying
    connection so every session sees the same in-memory database."""
    from app.models.agent import Agent
    from app.models.build import Build

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Agent.__table__.create(c))
        await conn.run_sync(lambda c: Build.__table__.create(c))

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await engine.dispose()


async def _add_agent(sf, *, current_build_id=None, status="online", enabled=True):
    from app.models.agent import Agent

    now = datetime.now(timezone.utc)
    agent_id = uuid.uuid4()
    async with sf() as db:
        db.add(Agent(
            id=agent_id,
            name=f"agent-{agent_id.hex[:8]}",
            status=status,
            enabled=enabled,
            connected_at=now if status == "online" else None,
            last_seen_at=now,
            current_build_id=current_build_id,
        ))
        await db.commit()
    return agent_id


async def _add_build(sf, *, status="pending", build_id=None):
    from app.models.build import Build

    build_id = build_id or uuid.uuid4()
    async with sf() as db:
        db.add(Build(
            id=build_id,
            pipeline_id=uuid.uuid4(),
            number=1,
            status=status,
        ))
        await db.commit()
    return build_id


async def _agent_reservation(sf, agent_id):
    from app.models.agent import Agent

    async with sf() as db:
        agent = await db.get(Agent, agent_id)
        return agent.current_build_id


# ---------------------------------------------------------------------------
# Layer 1: execute_build must not leak a pre-claimed agent when it bails
# ---------------------------------------------------------------------------
async def test_execute_build_releases_agent_when_build_no_longer_pending(session_factory):
    """Two dispatchers race for the same pending build and each pre-claim a
    *different* free agent. The winner flips the build to ``running``; the
    loser's worker reaches execute_build and finds the build is no longer
    pending. It must release the agent it pre-claimed — otherwise that agent
    is idle+online forever but the scheduler will never pick it again.
    """
    from app.services.build_executor import execute_build

    build_id = await _add_build(session_factory, status="running")  # winner started it
    loser_agent = await _add_agent(session_factory, current_build_id=build_id)

    await execute_build(build_id, session_factory=session_factory, claimed_agent_id=loser_agent)

    assert await _agent_reservation(session_factory, loser_agent) is None, (
        "execute_build leaked the pre-claimed agent: current_build_id was not "
        "cleared when the build was no longer pending"
    )


async def test_execute_build_releases_agent_when_build_missing(session_factory):
    """If the build row is gone by the time the worker runs (deleted mid-flight),
    a pre-claimed agent must still be released rather than leaked."""
    from app.services.build_executor import execute_build

    missing_build = uuid.uuid4()
    agent_id = await _add_agent(session_factory, current_build_id=missing_build)

    await execute_build(missing_build, session_factory=session_factory, claimed_agent_id=agent_id)

    assert await _agent_reservation(session_factory, agent_id) is None


# ---------------------------------------------------------------------------
# Layer 3: periodic reconciliation heals leaked reservations
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("terminal_status", ["success", "failed", "cancelled"])
async def test_reconcile_clears_reservation_for_terminal_build(session_factory, terminal_status):
    from app.services.agent_dispatcher import reconcile_stale_reservations

    build_id = await _add_build(session_factory, status=terminal_status)
    agent_id = await _add_agent(session_factory, current_build_id=build_id)

    async with session_factory() as db:
        cleared = await reconcile_stale_reservations(db)

    assert cleared == 1
    assert await _agent_reservation(session_factory, agent_id) is None


async def test_reconcile_clears_reservation_for_missing_build(session_factory):
    from app.services.agent_dispatcher import reconcile_stale_reservations

    agent_id = await _add_agent(session_factory, current_build_id=uuid.uuid4())

    async with session_factory() as db:
        cleared = await reconcile_stale_reservations(db)

    assert cleared == 1
    assert await _agent_reservation(session_factory, agent_id) is None


async def test_reconcile_keeps_reservation_for_running_build(session_factory):
    """A reservation for a build that is genuinely pending/running must NOT be
    cleared — we never want to yank an agent off live (or about-to-start) work."""
    from app.services.agent_dispatcher import reconcile_stale_reservations

    running = await _add_build(session_factory, status="running")
    pending = await _add_build(session_factory, status="pending")
    a_running = await _add_agent(session_factory, current_build_id=running)
    a_pending = await _add_agent(session_factory, current_build_id=pending)

    async with session_factory() as db:
        cleared = await reconcile_stale_reservations(db)

    assert cleared == 0
    assert await _agent_reservation(session_factory, a_running) == running
    assert await _agent_reservation(session_factory, a_pending) == pending


async def test_reconcile_noop_when_no_reservations(session_factory):
    from app.services.agent_dispatcher import reconcile_stale_reservations

    await _add_agent(session_factory)  # idle, no reservation
    async with session_factory() as db:
        cleared = await reconcile_stale_reservations(db)
    assert cleared == 0
