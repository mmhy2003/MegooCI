"""Regression test for the build-retry agent-routing bug.

Re-running a build must preserve the original build's ``runs_on`` constraint
so the retry lands on the same class of agent (e.g. a Linux-only pipeline must
not get rerun on a Windows agent). The retry copies the original build's frozen
stage/step graph rather than recompiling from current YAML, so the frozen
``runs_on`` snapshot must be copied alongside it.

Uses the same in-memory SQLite + ``@compiles`` shim as ``test_agent_dispatch``
so the real ``retry_build`` endpoint handler runs against a lightweight DB.
"""

import json
import os
import sqlite3
import sys
import types
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

# retry_build constructs a redis client from settings; give it a value so import
# and lazy client construction never blow up. publish_build_update is patched
# out below, so no real connection is ever attempted.
os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# Postgres ARRAY columns (Stage.artifact_paths) have no SQLite-side processor:
# binding a raw list fails, and on read ARRAY's result_processor iterates a
# returned string into a list of characters. So we (1) adapt lists to JSON text
# on write and (2) register a PARSE_DECLTYPES converter keyed to the ARRAY
# columns' unique declared type ("TEXTARRAY", see @compiles below) that loads
# them back into real lists *before* ARRAY's processor sees them. The distinct
# type name keeps this away from JSONB columns (declared "JSON"), whose own
# type already round-trips dicts correctly.
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_converter("TEXTARRAY", lambda b: json.loads(b.decode()))


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    # Unique declared type so the PARSE_DECLTYPES converter above only touches
    # ARRAY columns, not JSONB ("JSON") ones.
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, Stage, Step

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={
            "check_same_thread": False,
            "detect_types": sqlite3.PARSE_DECLTYPES,
        },
    )
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Build.__table__.create(c))
        await conn.run_sync(lambda c: Stage.__table__.create(c))
        await conn.run_sync(lambda c: Step.__table__.create(c))

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    """Neutralise retry_build's external side-effects (celery, search, redis).

    ``index_build``/``publish_build_update`` are imported *inside* the handler
    from modules that pull optional deps (meilisearch, etc.) absent from the
    dev venv, so we inject lightweight stub modules via ``sys.modules`` rather
    than patching attributes on the real modules.
    """
    async def _noop_async(*args, **kwargs):
        return None

    monkeypatch.setattr("app.api.v1.builds.run_build.delay", lambda *a, **k: None)

    stub_search = types.ModuleType("app.services.search")
    stub_search.index_build = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.search", stub_search)

    stub_notif = types.ModuleType("app.services.in_app_notifications")
    stub_notif.publish_build_update = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.in_app_notifications", stub_notif)

    # These tests exercise business logic (runs_on/artifact propagation), not RBAC.
    # Bypass the project-scoped access check so the SimpleNamespace user stub works.
    async def _dummy_project_id(db, build_id):
        return uuid.uuid4()

    monkeypatch.setattr("app.api.v1.builds.project_id_for_build", _dummy_project_id)
    monkeypatch.setattr("app.api.v1.builds.check_scoped_permission", lambda *a, **k: None)


async def _seed_original(sf, *, runs_on=None, stage_artifacts=None):
    from app.models.build import Build, Stage, Step

    build_id = uuid.uuid4()
    async with sf() as db:
        build = Build(
            id=build_id,
            pipeline_id=uuid.uuid4(),
            number=1,
            branch="main",
            commit_sha="abc123",
            status="success",
            trigger_type="manual",
            runs_on=runs_on,
        )
        db.add(build)
        await db.flush()

        stage = Stage(
            build_id=build.id, name="build", status="success", sort_order=0,
            artifact_paths=stage_artifacts,
        )
        db.add(stage)
        await db.flush()

        db.add(Step(
            stage_id=stage.id, name="compile", step_type="run",
            command="make", status="success", sort_order=0,
        ))
        await db.commit()
    return build_id


@pytest.mark.parametrize(
    "runs_on",
    [
        {"os": "linux"},
        {"os": "linux", "arch": "amd64", "labels": ["docker"]},
    ],
)
async def test_retry_preserves_runs_on(session_factory, runs_on):
    """The retried build must inherit the original's runs_on constraint."""
    from app.api.v1.builds import retry_build

    original_id = await _seed_original(session_factory, runs_on=runs_on)
    current_user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        new_build = await retry_build(original_id, db=db, current_user=current_user)

    assert new_build.id != original_id
    assert new_build.trigger_type == "retry"
    assert new_build.runs_on == runs_on, (
        "retry dropped the runs_on constraint — the rerun would be dispatched "
        "to any online agent regardless of os/arch/labels"
    )


async def test_retry_preserves_stage_artifact_paths(session_factory):
    """The retried build's stages must keep the original's artifact_paths,
    otherwise reruns silently collect no artifacts (build_executor only sends
    artifact_paths to the agent when stage.artifact_paths is set)."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.api.v1.builds import retry_build
    from app.models.build import Build, Stage

    artifacts = ["dist/**", "coverage/report.html"]
    original_id = await _seed_original(session_factory, stage_artifacts=artifacts)
    current_user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        new_build = await retry_build(original_id, db=db, current_user=current_user)

        result = await db.execute(
            select(Build)
            .where(Build.id == new_build.id)
            .options(selectinload(Build.stages))
        )
        reloaded = result.scalar_one()

    assert [s.artifact_paths for s in reloaded.stages] == [artifacts], (
        "retry dropped stage artifact_paths — rerun builds would collect no "
        "artifacts even though the original build did"
    )


async def test_retry_preserves_null_runs_on(session_factory):
    """A pipeline with no runs_on (None) must stay None on retry — not crash,
    and not invent a constraint."""
    from app.api.v1.builds import retry_build

    original_id = await _seed_original(session_factory, runs_on=None)
    current_user = types.SimpleNamespace(id=uuid.uuid4())

    async with session_factory() as db:
        new_build = await retry_build(original_id, db=db, current_user=current_user)

    assert new_build.runs_on is None
