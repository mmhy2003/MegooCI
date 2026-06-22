"""create_or_coalesce_build keeps at most one pending build per pipeline and
refreshes it to the latest trigger (latest wins)."""

import uuid

import pytest_asyncio
from sqlalchemy import func, select
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


async def test_creates_when_no_pending(session_factory):
    from app.services.build_concurrency import create_or_coalesce_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        build, created = await create_or_coalesce_build(
            db, pipeline_id=pid, default_branch="main", branch=None,
            commit_sha="aaa", params=None, triggered_by=None, trigger_type="webhook",
        )
        assert created is True
        assert build.status == "pending"
        assert build.branch == "main"   # falls back to default_branch
        assert build.number == 1
        await db.commit()  # caller commits the created build


async def test_coalesces_into_existing_pending(session_factory):
    from app.models.build import Build
    from app.services.build_concurrency import create_or_coalesce_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        first, c1 = await create_or_coalesce_build(
            db, pipeline_id=pid, default_branch="main", branch="main",
            commit_sha="aaa", params=None, triggered_by=None, trigger_type="manual")
        assert c1 is True
        await db.commit()

        second, c2 = await create_or_coalesce_build(
            db, pipeline_id=pid, default_branch="main", branch="main",
            commit_sha="bbb", params={"x": "1"}, triggered_by=None, trigger_type="webhook")
        assert c2 is False                 # coalesced, not created
        assert second.id == first.id       # same build row
        assert second.commit_sha == "bbb"  # latest wins
        assert second.params_json == {"x": "1"}
        assert second.trigger_type == "webhook"  # latest wins

        count = await db.scalar(
            select(func.count()).select_from(Build).where(
                Build.pipeline_id == pid, Build.status == "pending"))
        assert count == 1


async def test_index_rejects_a_direct_second_pending(session_factory):
    """Safety net behind the helper: the DB itself refuses a 2nd pending."""
    import pytest
    from sqlalchemy.exc import IntegrityError
    from app.models.build import Build

    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(Build(id=uuid.uuid4(), pipeline_id=pid, number=1,
                     status="pending", trigger_type="manual"))
        await db.commit()
        db.add(Build(id=uuid.uuid4(), pipeline_id=pid, number=2,
                     status="pending", trigger_type="manual"))
        with pytest.raises(IntegrityError):
            await db.commit()
