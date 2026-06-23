"""_run_build_stages must not start a build whose pipeline already has a
running build (Layer B atomic gate)."""

import uuid

import pytest_asyncio
from sqlalchemy import select
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


class _FakeRedis:
    """_run_build_stages must NOT touch redis before the start gate; any call
    here fails the test."""
    async def publish(self, *a, **k):  # pragma: no cover
        raise AssertionError("redis used before start gate")


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, Stage, Step

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Build, Stage, Step):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def test_run_build_stages_blocked_when_sibling_running(session_factory):
    from app.models.build import Build
    from app.services.build_executor import _run_build_stages

    pid = uuid.uuid4()
    blocked_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(Build(id=uuid.uuid4(), pipeline_id=pid, number=1, status="running",
                     trigger_type="manual"))
        db.add(Build(id=blocked_id, pipeline_id=pid, number=2, status="pending",
                     trigger_type="webhook"))
        await db.commit()

    await _run_build_stages(
        build_id=blocked_id, claimed_agent_id=uuid.uuid4(),
        session_factory=session_factory, redis_client=_FakeRedis(), channel="x",
    )

    async with session_factory() as db:
        b = await db.get(Build, blocked_id)
        assert b.status == "pending"  # gate left it pending; never ran
