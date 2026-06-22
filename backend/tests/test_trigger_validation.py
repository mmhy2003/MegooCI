"""trigger_build must reject invalid pipeline YAML with a 400 and create no build."""

import os
import sqlite3
import sys
import types
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

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
    # MUST match tests/test_build_retry.py — @compiles is process-global and
    # last-write-wins across collected modules; a divergent mapping breaks that
    # file's artifact_paths round-trip when the suites run together.
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Pipeline.__table__.create(c))
        await conn.run_sync(lambda c: Build.__table__.create(c))
        await conn.run_sync(lambda c: Stage.__table__.create(c))
        await conn.run_sync(lambda c: Step.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    async def _noop_async(*a, **k):
        return None

    monkeypatch.setattr("app.api.v1.builds.run_build.delay", lambda *a, **k: None)

    stub_search = types.ModuleType("app.services.search")
    stub_search.index_build = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.search", stub_search)

    stub_notif = types.ModuleType("app.services.in_app_notifications")
    stub_notif.publish_build_update = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.in_app_notifications", stub_notif)


async def _seed_pipeline(sf, yaml_content: str):
    from app.models.pipeline import Pipeline

    pid = uuid.uuid4()
    async with sf() as db:
        db.add(
            Pipeline(
                id=pid,
                project_id=uuid.uuid4(),
                name="p",
                default_branch="main",
                yaml_content=yaml_content,
                enabled=True,
                created_by=uuid.uuid4(),
            )
        )
        await db.commit()
    return pid


BAD_YAML = "name: demo\nstages:\n  - steps:\n      - run: echo hi\n"  # stage missing name
GOOD_YAML = "name: demo\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"


async def test_trigger_rejects_invalid_yaml(session_factory):
    from app.api.v1.builds import trigger_build
    from app.models.build import Build
    from app.schemas.build import BuildTriggerRequest

    pid = await _seed_pipeline(session_factory, BAD_YAML)
    user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await trigger_build(pid, BuildTriggerRequest(), db=db, current_user=user)

    assert exc_info.value.status_code == 400
    detail = exc_info.value.detail
    assert detail["message"] == "Pipeline validation failed"
    assert any("missing a 'name'" in e["message"] for e in detail["errors"])

    async with session_factory() as db:
        count = await db.scalar(select(func.count()).select_from(Build))
    assert count == 0, "no build should be created when YAML is invalid"


async def test_trigger_accepts_valid_yaml(session_factory):
    from app.api.v1.builds import trigger_build
    from app.schemas.build import BuildTriggerRequest

    pid = await _seed_pipeline(session_factory, GOOD_YAML)
    user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        build = await trigger_build(pid, BuildTriggerRequest(), db=db, current_user=user)

    assert build.status == "pending"
    assert build.number == 1
