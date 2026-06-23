"""Pipeline cascade-delete tests.

Runs the real handlers against in-memory SQLite using the same @compiles shims
as test_build_retry, plus PRAGMA foreign_keys=ON so ON DELETE CASCADE fires and
we can assert the cascade. Side effects (search index, agent cancel signal) are
stubbed.
"""
import json
import os
import sqlite3
import sys
import types
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
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
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    import app.models  # noqa: F401 - registers every table on Base.metadata
    from app.models.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={
            "check_same_thread": False,
            "detect_types": sqlite3.PARSE_DECLTYPES,
        },
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover - connection glue
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_side_effects(monkeypatch):
    """Stub the search index module and the agent cancel signal so handlers
    don't touch meilisearch / redis during tests."""
    async def _noop_async(*args, **kwargs):
        return None

    stub_search = types.ModuleType("app.services.search")
    stub_search.remove_pipeline = _noop_async
    stub_search.index_pipeline = _noop_async
    stub_search.index_build = _noop_async
    stub_search.remove_project = _noop_async
    stub_search.index_project = _noop_async
    monkeypatch.setitem(sys.modules, "app.services.search", stub_search)

    monkeypatch.setattr(
        "app.services.agent_dispatcher.signal_cancel_step",
        _noop_async,
        raising=False,
    )


async def _seed(sf, *, build_statuses=("success",), with_delivery=False):
    """Seed a user → project → pipeline → builds (one per status). Optionally
    attach a NotificationDelivery to the first build."""
    from app.models.build import Build
    from app.models.notification import NotificationChannel, NotificationDelivery
    from app.models.pipeline import Pipeline
    from app.models.project import Project
    from app.models.user import User

    async with sf() as db:
        user = User(email=f"u{uuid.uuid4().hex}@example.com", name="T", is_active=True)
        db.add(user)
        await db.flush()

        project = Project(
            name=f"Proj {uuid.uuid4().hex[:8]}",
            slug=f"proj-{uuid.uuid4().hex[:8]}",
            created_by=user.id,
        )
        db.add(project)
        await db.flush()

        pipeline = Pipeline(name="pipe", project_id=project.id, created_by=user.id)
        db.add(pipeline)
        await db.flush()

        build_ids = []
        for i, st in enumerate(build_statuses):
            build = Build(
                pipeline_id=pipeline.id, number=i + 1, status=st,
                trigger_type="manual",
            )
            db.add(build)
            await db.flush()
            build_ids.append(build.id)

        delivery_id = None
        if with_delivery and build_ids:
            channel = NotificationChannel(
                name=f"ch-{uuid.uuid4().hex[:8]}", channel_type="email",
                config_encrypted=b"x", created_by=user.id,
            )
            db.add(channel)
            await db.flush()
            delivery = NotificationDelivery(
                channel_id=channel.id, build_id=build_ids[0],
                message="m", status="sent",
            )
            db.add(delivery)
            await db.flush()
            delivery_id = delivery.id

        await db.commit()
        return types.SimpleNamespace(
            pipeline_id=pipeline.id, build_ids=build_ids,
            delivery_id=delivery_id, user_id=user.id,
        )


async def test_deleting_build_cascades_notification_deliveries(session_factory):
    """A NotificationDelivery must vanish with its build. Without ON DELETE
    CASCADE the build delete aborts on this FK, which is why pipelines with
    notified builds can't be deleted today."""
    from app.models.build import Build
    from app.models.notification import NotificationDelivery

    seed = await _seed(session_factory, build_statuses=("success",), with_delivery=True)

    async with session_factory() as db:
        build = await db.get(Build, seed.build_ids[0])
        await db.delete(build)
        await db.commit()
        gone = await db.get(NotificationDelivery, seed.delivery_id)

    assert gone is None, "notification_delivery was not cascade-deleted with its build"


async def test_notify_agents_of_cancel_signals_running_steps(session_factory, monkeypatch):
    """Only running steps that have an agent_id get a cancel signal."""
    from app.models.build import Stage, Step
    from app.services import agent_dispatcher

    seed = await _seed(session_factory, build_statuses=("running",))
    build_id = seed.build_ids[0]
    running_agent = uuid.uuid4()

    async with session_factory() as db:
        stage = Stage(build_id=build_id, name="s", status="running", sort_order=0)
        db.add(stage)
        await db.flush()
        db.add(Step(
            stage_id=stage.id, name="running-step", step_type="run",
            status="running", agent_id=running_agent, sort_order=0,
        ))
        db.add(Step(
            stage_id=stage.id, name="idle-step", step_type="run",
            status="pending", agent_id=uuid.uuid4(), sort_order=1,
        ))
        await db.commit()

    calls = []

    async def _spy(agent_id, step_id):
        calls.append((agent_id, step_id))

    monkeypatch.setattr(agent_dispatcher, "signal_cancel_step", _spy)

    async with session_factory() as db:
        await agent_dispatcher.notify_agents_of_cancel(db, build_id)

    assert len(calls) == 1, "exactly the one running step should be signalled"
    assert calls[0][0] == running_agent


async def _make_user_stub():
    return types.SimpleNamespace(id=uuid.uuid4())


async def test_delete_missing_pipeline_404(session_factory):
    from fastapi import HTTPException
    from app.api.v1.pipelines import delete_pipeline

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await delete_pipeline(
                uuid.uuid4(), force=False, db=db,
                _current_user=await _make_user_stub(),
            )
    assert exc.value.status_code == 404


async def test_delete_pipeline_without_builds_succeeds(session_factory):
    from app.api.v1.pipelines import delete_pipeline
    from app.models.pipeline import Pipeline

    seed = await _seed(session_factory, build_statuses=())  # no builds
    async with session_factory() as db:
        result = await delete_pipeline(
            seed.pipeline_id, force=False, db=db,
            _current_user=await _make_user_stub(),
        )
        assert result is None
        assert await db.get(Pipeline, seed.pipeline_id) is None


async def test_delete_pipeline_with_builds_requires_force(session_factory):
    from fastapi import HTTPException
    from app.api.v1.pipelines import delete_pipeline
    from app.models.build import Build
    from app.models.pipeline import Pipeline

    seed = await _seed(session_factory, build_statuses=("success",))
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc:
            await delete_pipeline(
                seed.pipeline_id, force=False, db=db,
                _current_user=await _make_user_stub(),
            )
    assert exc.value.status_code == 409
    assert exc.value.detail.lower().startswith("cannot delete pipeline")
    assert "1 build(s)" in exc.value.detail

    async with session_factory() as db:
        assert await db.get(Pipeline, seed.pipeline_id) is not None
        assert await db.get(Build, seed.build_ids[0]) is not None


async def test_force_delete_cascades_pipeline_builds_and_deliveries(session_factory):
    from app.api.v1.pipelines import delete_pipeline
    from app.models.build import Build
    from app.models.notification import NotificationDelivery
    from app.models.pipeline import Pipeline

    seed = await _seed(
        session_factory, build_statuses=("success", "failed"), with_delivery=True,
    )
    async with session_factory() as db:
        await delete_pipeline(
            seed.pipeline_id, force=True, db=db,
            _current_user=await _make_user_stub(),
        )

    async with session_factory() as db:
        assert await db.get(Pipeline, seed.pipeline_id) is None
        for bid in seed.build_ids:
            assert await db.get(Build, bid) is None
        assert await db.get(NotificationDelivery, seed.delivery_id) is None


async def test_force_delete_cancels_running_build(session_factory, monkeypatch):
    from app.api.v1.pipelines import delete_pipeline
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline
    from app.services import agent_dispatcher

    seed = await _seed(session_factory, build_statuses=("running",))
    build_id = seed.build_ids[0]
    agent_id = uuid.uuid4()
    async with session_factory() as db:
        stage = Stage(build_id=build_id, name="s", status="running", sort_order=0)
        db.add(stage)
        await db.flush()
        db.add(Step(
            stage_id=stage.id, name="run", step_type="run",
            status="running", agent_id=agent_id, sort_order=0,
        ))
        await db.commit()

    calls = []

    async def _spy(a_id, step_id):
        calls.append((a_id, step_id))

    monkeypatch.setattr(agent_dispatcher, "signal_cancel_step", _spy)

    async with session_factory() as db:
        await delete_pipeline(
            seed.pipeline_id, force=True, db=db,
            _current_user=await _make_user_stub(),
        )

    assert len(calls) == 1 and calls[0][0] == agent_id, (
        "the running build's agent should be signalled to cancel before delete"
    )
    async with session_factory() as db:
        assert await db.get(Pipeline, seed.pipeline_id) is None
        assert await db.get(Build, build_id) is None
