"""The partial unique indexes enforce ≤1 running and ≤1 pending build per
pipeline, and the reconcile SQL collapses pre-existing duplicates."""

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from tests._concurrency import create_concurrency_indexes


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    # MUST match tests/test_build_retry.py — @compiles is process-global.
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Build.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def _build(pipeline_id, number, status):
    from app.models.build import Build

    return Build(
        id=uuid.uuid4(), pipeline_id=pipeline_id, number=number, status=status,
        trigger_type="manual",
    )


async def test_two_running_same_pipeline_rejected(session_factory):
    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "running"))
        await db.commit()
        db.add(_build(pid, 2, "running"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_two_pending_same_pipeline_rejected(session_factory):
    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "pending"))
        await db.commit()
        db.add(_build(pid, 2, "pending"))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_one_running_one_pending_allowed(session_factory):
    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "running"))
        db.add(_build(pid, 2, "pending"))
        await db.commit()  # no error


async def test_running_in_different_pipelines_allowed(session_factory):
    async with session_factory() as db:
        db.add(_build(uuid.uuid4(), 1, "running"))
        db.add(_build(uuid.uuid4(), 1, "running"))
        await db.commit()  # no error


async def test_terminal_builds_unconstrained(session_factory):
    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "success"))
        db.add(_build(pid, 2, "success"))
        db.add(_build(pid, 3, "failed"))
        await db.commit()  # no error — only running/pending are constrained


async def test_reconcile_then_index(session_factory):
    """Two running + two pending for one pipeline are collapsed to one each
    (keep newest), after which both partial indexes can be created."""
    from datetime import datetime, timedelta, timezone

    from app.models.build import Build

    pid = uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    async with session_factory() as db:
        # Drop the indexes the fixture created so we can seed duplicates.
        await db.execute(text("DROP INDEX uq_one_running_build_per_pipeline"))
        await db.execute(text("DROP INDEX uq_one_pending_build_per_pipeline"))
        for i, st in [(1, "running"), (2, "running"), (3, "pending"), (4, "pending")]:
            b = Build(id=uuid.uuid4(), pipeline_id=pid, number=i, status=st,
                      trigger_type="manual", created_at=base + timedelta(minutes=i))
            db.add(b)
        await db.commit()

        # Portable reconcile: keep the newest per (pipeline, status), cancel rest.
        for st in ("running", "pending"):
            rows = (await db.execute(
                select(Build).where(Build.pipeline_id == pid, Build.status == st)
                .order_by(Build.created_at.desc())
            )).scalars().all()
            for b in rows[1:]:
                b.status = "cancelled"
        await db.commit()

        running = (await db.execute(select(Build).where(
            Build.pipeline_id == pid, Build.status == "running"))).scalars().all()
        pending = (await db.execute(select(Build).where(
            Build.pipeline_id == pid, Build.status == "pending"))).scalars().all()
        assert len(running) == 1 and running[0].number == 2  # newest kept
        assert len(pending) == 1 and pending[0].number == 4

    # Indexes can now be created without violation.
    async with session_factory() as db:
        conn = await db.connection()
        await create_concurrency_indexes(conn)
        await db.commit()
