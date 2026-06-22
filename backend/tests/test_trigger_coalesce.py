"""A manual trigger while the pipeline already has a queued (pending) run
coalesces into it instead of creating a second build."""

import os
import sys
import types
import uuid

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from tests._concurrency import create_concurrency_indexes

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")


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
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Pipeline, Build, Stage, Step):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    async def _noop(*a, **k):
        return None

    monkeypatch.setattr("app.api.v1.builds.run_build.delay", lambda *a, **k: None)
    stub_search = types.ModuleType("app.services.search")
    stub_search.index_build = _noop
    monkeypatch.setitem(sys.modules, "app.services.search", stub_search)
    stub_notif = types.ModuleType("app.services.in_app_notifications")
    stub_notif.publish_build_update = _noop
    monkeypatch.setitem(sys.modules, "app.services.in_app_notifications", stub_notif)


async def _seed_pipeline(sf, yaml_content=None):
    from app.models.pipeline import Pipeline
    pid = uuid.uuid4()
    async with sf() as db:
        db.add(Pipeline(id=pid, project_id=uuid.uuid4(), name="p",
                        default_branch="main", yaml_content=yaml_content,
                        enabled=True, created_by=uuid.uuid4()))
        await db.commit()
    return pid


async def test_manual_trigger_coalesces_into_pending(session_factory):
    from app.api.v1.builds import trigger_build
    from app.models.build import Build
    from app.schemas.build import BuildTriggerRequest

    pid = await _seed_pipeline(session_factory)  # no YAML → stageless builds
    user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        b1 = await trigger_build(pid, BuildTriggerRequest(commit_sha="aaa"),
                                 db=db, current_user=user)
    async with session_factory() as db:
        b2 = await trigger_build(pid, BuildTriggerRequest(commit_sha="bbb"),
                                 db=db, current_user=user)

    assert b2.id == b1.id            # coalesced into the same queued build
    assert b2.commit_sha == "bbb"    # latest wins
    async with session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(Build).where(
            Build.pipeline_id == pid, Build.status == "pending"))
    assert count == 1
