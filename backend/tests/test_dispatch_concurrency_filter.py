"""dispatch_pending_builds skips pending builds whose pipeline already has a
running build."""

import uuid

import pytest_asyncio
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from tests._concurrency import create_concurrency_indexes


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.agent import Agent
    from app.models.build import Build

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Agent, Build):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
def _patch(monkeypatch):
    async def _no_maint(db):
        return False
    monkeypatch.setattr("app.api.v1.system.is_maintenance_mode", _no_maint)


def _build(pid, number, status, created_at):
    from app.models.build import Build
    return Build(id=uuid.uuid4(), pipeline_id=pid, number=number, status=status,
                 trigger_type="webhook", created_at=created_at)


async def test_dispatchable_stmt_excludes_busy_pipelines(session_factory):
    """The candidate query returns the idle pipeline's pending build and omits
    the busy pipeline's pending build (its sibling is running)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select
    from app.services.build_concurrency import dispatchable_pending_builds_stmt

    pid_busy, pid_idle = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    idle_pending_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid_busy, 1, "running", base))
        db.add(_build(pid_busy, 2, "pending", base + timedelta(minutes=1)))
        idle = _build(pid_idle, 1, "pending", base + timedelta(minutes=2))
        idle.id = idle_pending_id
        db.add(idle)
        await db.commit()

        rows = (await db.execute(dispatchable_pending_builds_stmt())).scalars().all()
        ids = {b.id for b in rows}
        assert idle_pending_id in ids                      # idle pipeline dispatchable
        assert all(b.pipeline_id != pid_busy for b in rows)  # busy pipeline excluded


async def test_dispatch_runs_clean_with_no_agents(session_factory):
    """Sanity: the rewired dispatcher runs without error when no agents exist."""
    from datetime import datetime, timezone
    from app.services import agent_dispatcher

    async with session_factory() as db:
        db.add(_build(uuid.uuid4(), 1, "pending", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        await db.commit()
    await agent_dispatcher.dispatch_pending_builds(session_factory=session_factory)


async def test_dispatch_single_build_blocked_when_pipeline_running(session_factory, monkeypatch):
    """dispatch_single_build refuses a build whose pipeline already runs one."""
    from datetime import datetime, timedelta, timezone
    from app.services import agent_dispatcher

    # dispatch_single_build uses the module-global async_session; point it at ours.
    monkeypatch.setattr("app.database.async_session", session_factory, raising=False)

    pid = uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    blocked_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "running", base))
        b = _build(pid, 2, "pending", base + timedelta(minutes=1))
        b.id = blocked_id
        db.add(b)
        await db.commit()

    assert await agent_dispatcher.dispatch_single_build(blocked_id) is False
