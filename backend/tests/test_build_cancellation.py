"""Cancellation: flag helpers, executor stop-advancing, gate bail-out.

Runs the real services against in-memory SQLite using the same @compiles
shims as the other dispatcher/executor tests.
"""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

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
    return "JSON"


class FakeAsyncRedis:
    """Minimal dict-backed stand-in for redis.asyncio used in cancel tests."""
    def __init__(self, store=None):
        self.store = dict(store or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, *a, **k):
        return 0

    async def aclose(self):
        pass


@pytest_asyncio.fixture
async def session_factory():
    from app.models.agent import Agent
    from app.models.build import Build, Stage, Step

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Agent, Build, Stage, Step):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def test_set_and_read_cancel_flag():
    from app.services.agent_dispatcher import (
        build_cancel_flag_key, set_build_cancel_flag, build_cancel_requested,
    )
    redis = FakeAsyncRedis()
    bid = uuid.uuid4()

    assert await build_cancel_requested(redis, bid) is False
    await set_build_cancel_flag(redis, bid)
    assert redis.store[build_cancel_flag_key(bid)] == "1"
    assert await build_cancel_requested(redis, bid) is True


async def test_signal_build_cancel_sets_flag_and_notifies(session_factory, monkeypatch):
    from app.services import agent_dispatcher
    from app.services.agent_dispatcher import (
        build_cancel_flag_key, signal_build_cancel,
    )

    notified = []

    async def _spy_notify(db, build_id):
        notified.append(build_id)

    monkeypatch.setattr(agent_dispatcher, "notify_agents_of_cancel", _spy_notify)

    redis = FakeAsyncRedis()
    bid = uuid.uuid4()
    async with session_factory() as db:
        await signal_build_cancel(db, bid, redis)

    assert redis.store[build_cancel_flag_key(bid)] == "1"
    assert notified == [bid]


async def _seed_build(sf, *, n_stages, steps_per_stage):
    from app.models.build import Build, Stage, Step
    pid = uuid.uuid4()
    bid = uuid.uuid4()
    async with sf() as db:
        db.add(Build(id=bid, pipeline_id=pid, number=1, status="pending",
                     trigger_type="manual", triggered_by=None))
        await db.flush()
        for si in range(n_stages):
            stage = Stage(id=uuid.uuid4(), build_id=bid, name=f"stage{si}",
                          status="pending", sort_order=si)
            db.add(stage)
            await db.flush()
            for pi in range(steps_per_stage):
                db.add(Step(id=uuid.uuid4(), stage_id=stage.id, name=f"s{si}-{pi}",
                            step_type="run", status="pending", sort_order=pi))
        await db.commit()
    return bid


def _patch_executor(monkeypatch, fake_step):
    async def _scope(db, build):
        return {}, {}, {}
    monkeypatch.setattr("app.services.build_executor._load_scope_context", _scope)
    monkeypatch.setattr("app.services.build_executor._execute_step", fake_step)


async def test_executor_stops_advancing_on_cancel(session_factory, monkeypatch):
    """Cancel during the first step → second step never runs; build, active
    stage, and the un-run step all end 'cancelled' (not 'success')."""
    from app.models.build import Build, Stage, Step
    from app.services.build_executor import _run_build_stages
    from app.services.step_actions.base import StepResult
    from sqlalchemy import select

    bid = await _seed_build(session_factory, n_stages=1, steps_per_stage=2)
    calls = {"n": 0}

    async def fake_step(*, step, build, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate the cancel endpoint committing from another session.
            async with session_factory() as db2:
                b = await db2.get(Build, build.id)
                b.status = "cancelled"
                await db2.commit()
        return StepResult(exit_code=0, status="success")

    _patch_executor(monkeypatch, fake_step)

    await _run_build_stages(
        build_id=bid, claimed_agent_id=uuid.uuid4(),
        session_factory=session_factory, redis_client=FakeAsyncRedis(), channel="x",
    )

    assert calls["n"] == 1, "second step must not run after cancel"
    async with session_factory() as db:
        b = await db.get(Build, bid)
        assert b.status == "cancelled"
        stages = (await db.execute(select(Stage).where(Stage.build_id == bid))).scalars().all()
        assert stages[0].status == "cancelled", "stage must not be stamped success on cancel"
        steps = (await db.execute(select(Step).order_by(Step.sort_order))).scalars().all()
        assert steps[1].status == "cancelled", "un-run step should be flipped to cancelled"


async def test_executor_cancel_during_last_step(session_factory, monkeypatch):
    """Cancel that lands while the only step runs → stage finalized 'cancelled'
    via the post-loop refresh, not 'success'."""
    from app.models.build import Build, Stage
    from app.services.build_executor import _run_build_stages
    from app.services.step_actions.base import StepResult
    from sqlalchemy import select

    bid = await _seed_build(session_factory, n_stages=1, steps_per_stage=1)

    async def fake_step(*, step, build, **kw):
        async with session_factory() as db2:
            b = await db2.get(Build, build.id)
            b.status = "cancelled"
            await db2.commit()
        return StepResult(exit_code=0, status="success")

    _patch_executor(monkeypatch, fake_step)

    await _run_build_stages(
        build_id=bid, claimed_agent_id=uuid.uuid4(),
        session_factory=session_factory, redis_client=FakeAsyncRedis(), channel="x",
    )

    async with session_factory() as db:
        assert (await db.get(Build, bid)).status == "cancelled"
        stage = (await db.execute(select(Stage).where(Stage.build_id == bid))).scalars().first()
        assert stage.status == "cancelled"


async def test_executor_cancelled_step_result_without_real_cancel_continues(
    session_factory, monkeypatch
):
    """A step returning status='cancelled' on its OWN timeout (e.g. ai_agent)
    must NOT abort the build when no cancel was requested — prior behaviour."""
    from app.models.build import Build
    from app.services.build_executor import _run_build_stages
    from app.services.step_actions.base import StepResult

    bid = await _seed_build(session_factory, n_stages=1, steps_per_stage=2)
    calls = {"n": 0}

    async def fake_step(*, step, build, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return StepResult(exit_code=-1, status="cancelled")  # no DB flip
        return StepResult(exit_code=0, status="success")

    _patch_executor(monkeypatch, fake_step)

    await _run_build_stages(
        build_id=bid, claimed_agent_id=uuid.uuid4(),
        session_factory=session_factory, redis_client=FakeAsyncRedis(), channel="x",
    )

    assert calls["n"] == 2, "second step should still run when build wasn't cancelled"
    async with session_factory() as db:
        assert (await db.get(Build, bid)).status == "success"


def _make_ctx(build_id):
    from app.services.step_actions.base import StepContext
    return StepContext(
        build_id=build_id, step_id=uuid.uuid4(), step_name="gate",
        stage_name="s", pipeline_id=uuid.uuid4(), project_id=uuid.uuid4(),
        branch="main", commit_sha=None, env={}, secrets={},
    )


async def _drain_handler(handler, config, ctx, db):
    from app.services.step_actions.base import StepResult
    last = None
    async for item in handler.execute(config, ctx, db):
        if isinstance(item, StepResult):
            last = item
    return last


async def test_wait_webhook_bails_on_cancel_flag(monkeypatch):
    import app.services.step_actions.wait as wait_mod
    from app.services.agent_dispatcher import build_cancel_flag_key

    bid = uuid.uuid4()
    fake = FakeAsyncRedis({build_cancel_flag_key(bid): "1"})
    monkeypatch.setattr(wait_mod.aioredis, "from_url", lambda *a, **k: fake)

    result = await _drain_handler(
        wait_mod.WaitWebhookHandler(), {"timeout": 5, "name": "cb"},
        _make_ctx(bid), None,
    )
    assert result is not None and result.status == "cancelled"


async def test_wait_input_bails_on_cancel_flag(monkeypatch):
    import app.services.step_actions.wait as wait_mod
    from app.services.agent_dispatcher import build_cancel_flag_key

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(wait_mod, "_resolve_approver_user_ids", _noop)
    monkeypatch.setattr(wait_mod, "notify_users", _noop)

    bid = uuid.uuid4()
    fake = FakeAsyncRedis({build_cancel_flag_key(bid): "1"})
    monkeypatch.setattr(wait_mod.aioredis, "from_url", lambda *a, **k: fake)

    result = await _drain_handler(
        wait_mod.WaitInputHandler(), {"timeout": 5, "prompt": "ok", "allowed_users": []},
        _make_ctx(bid), None,
    )
    assert result is not None and result.status == "cancelled"
