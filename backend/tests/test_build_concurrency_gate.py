"""Start-gate helpers: detect a running sibling, and atomically claim the
single running slot per pipeline."""

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
    from app.models.build import Build

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Build.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _build(pid, number, status):
    from app.models.build import Build
    return Build(id=uuid.uuid4(), pipeline_id=pid, number=number, status=status,
                 trigger_type="manual")


async def test_pipeline_has_running_build(session_factory):
    from app.services.build_concurrency import pipeline_has_running_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        running = _build(pid, 1, "running")
        db.add(running)
        await db.commit()
        assert await pipeline_has_running_build(db, pid) is True
        # excluding the only running build reports no *other* running build
        assert await pipeline_has_running_build(db, pid, exclude_build_id=running.id) is False
        assert await pipeline_has_running_build(db, uuid.uuid4()) is False


async def test_try_start_build_succeeds_when_idle(session_factory):
    from app.services.build_concurrency import try_start_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        b = _build(pid, 1, "pending")
        db.add(b)
        await db.commit()
        assert await try_start_build(db, b) is True
        assert b.status == "running"
        assert b.started_at is not None


async def test_try_start_build_blocked_when_sibling_running(session_factory):
    from app.services.build_concurrency import try_start_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "running"))
        b = _build(pid, 2, "pending")
        db.add(b)
        await db.commit()
        assert await try_start_build(db, b) is False
        await db.refresh(b)
        assert b.status == "pending"  # left untouched
