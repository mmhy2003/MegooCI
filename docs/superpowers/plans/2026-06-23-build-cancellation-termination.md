# Build Cancellation Termination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make cancelling a build actually stop its stages and steps — the executor stops advancing, the in-flight step's whole process tree is killed on the agent, and builds parked on a server-side gate cancel promptly.

**Architecture:** Cooperative cancellation. The DB `build.status="cancelled"` is the source of truth. The executor re-reads it from the DB at each stage/step boundary (today it reads a stale in-memory ORM attribute that is never refreshed). A per-build Redis cancel flag is the fast fan-out signal for long-lived server-side gate loops. The existing per-agent `cancel_step` push is kept, upgraded so the Go agent kills the whole process group rather than just the shell.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 async / Celery / Redis (asyncio); Go 1.22 agent; pytest (`asyncio_mode=auto`, in-memory SQLite with `@compiles` shims); `go test`.

## Global Constraints

- **Celery loop-binding:** async DB helpers invoked from a Celery task must use the `db`/`session_factory` passed down (the worker's loop-bound engine), never `from app.database import async_session`. New cancel helpers take `db` and `redis_client` as parameters.
- **Postgres source of truth:** `build.status` (and `stage`/`step.status`) remain authoritative for final state. The Redis flag is only a fast signal; never the system of record.
- **Cancel flag key:** `build:{build_id}:cancel`, value `"1"`, TTL `90000` seconds (> the 86400s `wait_input` default so it self-cleans, never expiring mid-gate).
- **Cross-platform agent:** the agent runs on Linux and Windows. Process-tree kill is split by build tag (`//go:build !windows` vs `//go:build windows`). No new Go module dependencies (use stdlib + `taskkill` on Windows).
- **Test DB shims:** in-memory SQLite tests register the Postgres-type `@compiles` shims (`UUID`→`CHAR(32)`, `JSONB`/`JSON`→`JSON`, `ARRAY`→`JSON`/`TEXTARRAY`) exactly as the existing tests do, and set `os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")` at import.

**Design refinement over the spec (intentional):** the spec's "treat a `cancelled` step result as a stop" (component 2, rule 3) is implemented by re-reading the authoritative DB status at boundaries rather than by branching on the step's returned status. A real cancel always commits `build.status="cancelled"` *before* signalling the agent, so the next boundary refresh catches it. This also correctly distinguishes a genuine cancel from an `ai_agent` step that returns `status="cancelled"` on its *own* timeout (`local.go runAiAgent`) — the latter must not abort the whole build, preserving today's behaviour.

---

### Task 1: Cancel-signal helpers in `agent_dispatcher`

Add the per-build cancel flag (set/read) and the `signal_build_cancel` fan-out that both the cancel endpoint and the cascade-delete path will call.

**Files:**
- Modify: `backend/app/services/agent_dispatcher.py` (add functions near `signal_cancel_step` / `notify_agents_of_cancel`, ~line 466)
- Test: `backend/tests/test_build_cancellation.py` (new)

**Interfaces:**
- Consumes: existing `notify_agents_of_cancel(db, build_id)`.
- Produces:
  - `build_cancel_flag_key(build_id) -> str`
  - `async set_build_cancel_flag(redis_client, build_id) -> None`
  - `async build_cancel_requested(redis_client, build_id) -> bool`
  - `async signal_build_cancel(db, build_id, redis_client) -> None`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_cancellation.py`:

```python
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
```

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_cancel_flag_key'`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_build_cancellation.py::test_set_and_read_cancel_flag tests/test_build_cancellation.py::test_signal_build_cancel_sets_flag_and_notifies -v`
Expected: FAIL with ImportError on the new names.

- [ ] **Step 3: Add the helpers**

In `backend/app/services/agent_dispatcher.py`, add after `signal_cancel_step` (around line 485):

```python
# How long the per-build cancel flag lives in Redis. Longer than the longest
# server-side gate (wait_input defaults to 86400s) so it never expires while a
# build is still parked on a gate, but bounded so it self-cleans afterwards.
_CANCEL_FLAG_TTL_SECONDS = 90000


def build_cancel_flag_key(build_id: uuid.UUID | str) -> str:
    """Redis key set when a build has been cancelled. Long-lived server-side
    gate loops poll this so they can bail without holding a DB transaction
    open for the gate's whole (up-to-24h) lifetime."""
    return f"build:{build_id}:cancel"


async def set_build_cancel_flag(redis_client, build_id: uuid.UUID) -> None:
    """Mark a build cancelled for fast fan-out to gate loops."""
    await redis_client.set(
        build_cancel_flag_key(build_id), "1", ex=_CANCEL_FLAG_TTL_SECONDS
    )


async def build_cancel_requested(redis_client, build_id: uuid.UUID) -> bool:
    """True if a cancel has been signalled for *build_id*."""
    return await redis_client.get(build_cancel_flag_key(build_id)) is not None


async def signal_build_cancel(
    db: AsyncSession, build_id: uuid.UUID, redis_client
) -> None:
    """Fan out a build cancellation: set the Redis cancel flag (so gate loops
    bail) and push cancel frames to any agent running this build's steps.

    *redis_client* is passed in so this stays usable from both the API process
    and a Celery worker without importing a loop-bound global.
    """
    await set_build_cancel_flag(redis_client, build_id)
    await notify_agents_of_cancel(db, build_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_build_cancellation.py::test_set_and_read_cancel_flag tests/test_build_cancellation.py::test_signal_build_cancel_sets_flag_and_notifies -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_dispatcher.py backend/tests/test_build_cancellation.py
git commit -m "feat(builds): add per-build cancel flag + signal_build_cancel helper"
```

---

### Task 2: Broaden `notify_agents_of_cancel` to the reserved agent

Close the dispatch→`step_started` race: a just-dispatched step has no `agent_id` yet, so today's cancel misses it. Fall back to the agent reserved for the build (`Agent.current_build_id == build_id`).

**Files:**
- Modify: `backend/app/services/agent_dispatcher.py:487-507` (`notify_agents_of_cancel`)
- Test: `backend/tests/test_pipeline_cascade_delete.py` (update existing `test_notify_agents_of_cancel_signals_running_steps`, add one)

**Interfaces:**
- Consumes: `signal_cancel_step(agent_id, step_id)` (existing).
- Produces: same `notify_agents_of_cancel(db, build_id)` signature; new behaviour — a running step with `agent_id IS NULL` is signalled via the reserved agent.

- [ ] **Step 1: Update the existing test and add the race test**

In `backend/tests/test_pipeline_cascade_delete.py`, the existing `test_notify_agents_of_cancel_signals_running_steps` (line 174) currently seeds a running step *with* `agent_id` and a pending step. Keep it (a running step with an agent is still signalled exactly once). Add a new test below it:

```python
async def test_notify_agents_of_cancel_falls_back_to_reserved_agent(
    session_factory, monkeypatch
):
    """A running step whose step_started hasn't landed yet (agent_id is NULL)
    must still be cancelled — via the agent reserved for the build."""
    from app.models.agent import Agent
    from app.models.build import Stage, Step
    from app.services import agent_dispatcher

    seed = await _seed(session_factory, build_statuses=("running",))
    build_id = seed.build_ids[0]
    reserved_agent = uuid.uuid4()

    async with session_factory() as db:
        stage = Stage(build_id=build_id, name="s", status="running", sort_order=0)
        db.add(stage)
        await db.flush()
        db.add(Step(
            stage_id=stage.id, name="just-dispatched", step_type="run",
            status="running", agent_id=None, sort_order=0,
        ))
        db.add(Agent(
            id=reserved_agent, name=f"agent-{reserved_agent.hex[:8]}",
            status="online", current_build_id=build_id,
        ))
        await db.commit()

    calls = []

    async def _spy(agent_id, step_id):
        calls.append((agent_id, step_id))

    monkeypatch.setattr(agent_dispatcher, "signal_cancel_step", _spy)

    async with session_factory() as db:
        await agent_dispatcher.notify_agents_of_cancel(db, build_id)

    assert len(calls) == 1, "the running step should be signalled via the reserved agent"
    assert calls[0][0] == reserved_agent
```

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py::test_notify_agents_of_cancel_falls_back_to_reserved_agent -v`
Expected: FAIL — no signal sent (current code filters `Step.agent_id IS NOT NULL`), `len(calls) == 0`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py -v`
Expected: the new test FAILS (`assert 0 == 1`); the existing tests still PASS.

- [ ] **Step 3: Broaden the implementation**

Replace `notify_agents_of_cancel` (`agent_dispatcher.py:487-507`) with:

```python
async def notify_agents_of_cancel(db: AsyncSession, build_id: uuid.UUID) -> None:
    """Publish cancel frames for every running step of ``build_id``. Best-effort:
    per-step errors are swallowed so callers (single build cancel, pipeline
    cascade-delete) never fail on a flaky agent channel.

    A step's ``agent_id`` is only set once the agent's ``step_started`` frame
    lands, which can lag a fraction of a second behind dispatch. To avoid
    missing a step in that window we fall back to the agent reserved for the
    build (``Agent.current_build_id == build_id``)."""
    from app.models.build import Stage, Step

    reserved_agent_id = await db.scalar(
        select(Agent.id).where(Agent.current_build_id == build_id)
    )

    result = await db.execute(
        select(Step)
        .join(Stage, Step.stage_id == Stage.id)
        .where(
            Stage.build_id == build_id,
            Step.status == "running",
        )
    )
    for step in result.scalars().all():
        agent_id = step.agent_id or reserved_agent_id
        if agent_id is None:
            continue
        try:
            await signal_cancel_step(agent_id, step.id)
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py -v`
Expected: PASS (all, including the new fallback test and the unchanged `test_force_delete_cancels_running_build`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_dispatcher.py backend/tests/test_pipeline_cascade_delete.py
git commit -m "fix(builds): cancel signal falls back to reserved agent (closes dispatch race)"
```

---

### Task 3: Wire `signal_build_cancel` into the cancel endpoint and cascade-delete

Both paths that flip a build to `cancelled` must also raise the Redis flag, not just push to agents.

**Files:**
- Modify: `backend/app/api/v1/builds.py:171-196` (cancel endpoint)
- Modify: `backend/app/api/v1/pipelines.py:216-234` (force-delete cancel block)
- Test: `backend/tests/test_pipeline_cascade_delete.py` (update autouse `_patch_side_effects`)

**Interfaces:**
- Consumes: `signal_build_cancel(db, build_id, redis_client)` from Task 1.

- [ ] **Step 1: Make the cascade-delete tests tolerate the flag set**

The autouse `_patch_side_effects` fixture (`test_pipeline_cascade_delete.py:75`) stubs `signal_cancel_step`. After this task the cascade-delete calls `signal_build_cancel`, which also calls `set_build_cancel_flag` (touches Redis). Stub that too so tests don't hit a real Redis. Add inside `_patch_side_effects`, after the existing `signal_cancel_step` patch:

```python
    monkeypatch.setattr(
        "app.services.agent_dispatcher.set_build_cancel_flag",
        _noop_async,
        raising=False,
    )
```

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py -v`
Expected: still PASS (no behaviour change yet; the stub is harmless).

- [ ] **Step 2: Wire the cancel endpoint**

In `backend/app/api/v1/builds.py`, the cancel endpoint currently (lines 178-198) calls `notify_agents_of_cancel`, then separately builds `_redis` for `publish_build_update`, then `return build`. Reorder so one `_redis` client serves both, and call `signal_build_cancel`. Replace the whole block from the `# If any step...` comment (line 178) through `return build` (line 198) with:

```python
    # Stop the running pipeline: raise the cancel flag (server-side gates poll
    # it) and push cancel frames to any agent running this build's steps. The
    # executor re-reads build.status at each step/stage boundary and bails.
    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.agent_dispatcher import signal_build_cancel
    from app.services.in_app_notifications import publish_build_update

    settings = get_settings()
    _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        await signal_build_cancel(db, build_id, _redis)
        # Best-effort: publish cancellation to the global builds:updates channel.
        try:
            await publish_build_update(_redis, build)
        except Exception:
            pass
    finally:
        await _redis.aclose()

    return build
```

(Delete the old `from app.services.agent_dispatcher import notify_agents_of_cancel` / `await notify_agents_of_cancel(...)` lines and the now-duplicated `_redis` block they preceded.)

- [ ] **Step 3: Wire the cascade-delete path**

In `backend/app/api/v1/pipelines.py`, replace the cancel block (lines 220-234) so it raises the flag per build via `signal_build_cancel`:

```python
        from app.services.agent_dispatcher import signal_build_cancel
        from app.config import get_settings
        import redis.asyncio as aioredis

        active_result = await db.execute(
            select(Build).where(
                Build.pipeline_id == pipeline_id,
                Build.status.in_(("pending", "queued", "running")),
            )
        )
        active_builds = list(active_result.scalars().all())
        if active_builds:
            for build in active_builds:
                build.status = "cancelled"
            await db.commit()
            settings = get_settings()
            _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
            try:
                for build in active_builds:
                    await signal_build_cancel(db, build.id, _redis)
            finally:
                await _redis.aclose()
```

Keep the surrounding `if force and build_count > 0:` guard and the existing `select`/status set exactly as they were — only the import and the per-build signalling change. (If the original `select` already matches the statuses above, leave it; this block is shown complete for clarity.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py -v`
Expected: PASS — in particular `test_force_delete_cancels_running_build` still sees exactly one `signal_cancel_step` call (now routed through `signal_build_cancel` → `notify_agents_of_cancel`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/builds.py backend/app/api/v1/pipelines.py backend/tests/test_pipeline_cascade_delete.py
git commit -m "feat(builds): raise cancel flag on cancel + cascade-delete via signal_build_cancel"
```

---

### Task 4: Executor observes cancellation at boundaries

The heart of the fix. Re-read `build.status` from the DB before each stage and step, finalize cancel-aware, and flip un-run stages/steps to `cancelled`.

**Files:**
- Modify: `backend/app/services/build_executor.py:199-357` (`_run_build_stages`) and add `_cancel_remaining`
- Test: `backend/tests/test_build_cancellation.py` (append)

**Interfaces:**
- Consumes: existing `_execute_step`, `_load_scope_context`, `try_start_build`, `_publish`.
- Produces: `_run_build_stages` that stops on cancel; new module-level `async _cancel_remaining(db, build) -> None`.

- [ ] **Step 1: Write the failing executor tests**

Append to `backend/tests/test_build_cancellation.py`:

```python
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
```

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -k executor -v`
Expected: FAIL — current `_run_build_stages` runs both steps and stamps the stage `success` (`calls["n"] == 2`, stage `success`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -k executor -v`
Expected: `test_executor_stops_advancing_on_cancel` and `test_executor_cancel_during_last_step` FAIL; `test_executor_cancelled_step_result_without_real_cancel_continues` may already pass.

- [ ] **Step 3: Rewrite the executor loop**

In `backend/app/services/build_executor.py`, replace the body from `build_failed = False` (line 238) through the `build.status = final_status` block (line 334) with the version below. The lines *before* (load + `try_start_build` + `build_started` publish + `_load_scope_context`) and *after* (`build_finished` publish, `publish_build_update`, `send_build_finished` loop, `_send_build_finished_notification`) are unchanged.

```python
        build_failed = False
        cancelled = False
        build_agent_ids: set[uuid.UUID] = set()

        for stage in sorted(build.stages, key=lambda s: s.sort_order):
            # Re-read the authoritative status: the cancel endpoint commits
            # build.status="cancelled" from a *different* session, and this
            # worker session has expire_on_commit=False, so the in-memory copy
            # would otherwise never change (this is the original bug).
            await db.refresh(build, ["status"])
            if build.status == "cancelled":
                cancelled = True
                break

            stage.status = "running"
            stage.started_at = datetime.now(timezone.utc)
            await db.commit()

            await _publish(redis_client, channel, {
                "event": "stage_started",
                "stage_id": str(stage.id),
                "stage_name": stage.name,
            })

            stage_failed = False

            for step in sorted(stage.steps, key=lambda s: s.sort_order):
                await db.refresh(build, ["status"])
                if build.status == "cancelled":
                    cancelled = True
                    break

                step.status = "running"
                step.started_at = datetime.now(timezone.utc)
                await db.commit()

                await _publish(redis_client, channel, {
                    "event": "step_started",
                    "step_id": str(step.id),
                    "step_name": step.name,
                    "step_type": step.step_type,
                })

                step_result = await _execute_step(
                    step=step,
                    stage=stage,
                    build=build,
                    secrets=secrets,
                    env_vars=env_vars,
                    builtins=builtins,
                    db=db,
                    redis_client=redis_client,
                    channel=channel,
                    build_agent_ids=build_agent_ids,
                    claimed_agent_id=claimed_agent_id,
                )

                if step_result.error:
                    await _emit_system_log(
                        step, db, redis_client, channel,
                        f"❌ {step_result.error}",
                    )

                step.exit_code = step_result.exit_code
                step.status = step_result.status
                step.finished_at = datetime.now(timezone.utc)
                await db.commit()

                await _publish(redis_client, channel, {
                    "event": "step_finished",
                    "step_id": str(step.id),
                    "status": step.status,
                    "exit_code": step.exit_code,
                })

                if step.status == "failed":
                    stage_failed = True
                    break

            # A cancel can land while the *last* step of the stage is running,
            # after that step's top-of-loop check. Re-read once more so the
            # stage isn't wrongly finalized as success.
            if not cancelled:
                await db.refresh(build, ["status"])
                cancelled = build.status == "cancelled"

            if cancelled:
                stage.status = "cancelled"
            else:
                stage.status = "failed" if stage_failed else "success"
            stage.finished_at = datetime.now(timezone.utc)
            await db.commit()

            await _publish(redis_client, channel, {
                "event": "stage_finished",
                "stage_id": str(stage.id),
                "status": stage.status,
            })

            if cancelled:
                break
            if stage_failed:
                build_failed = True
                break

        # Refresh only the status column — a full refresh() could expire the
        # eager-loaded stages/steps collections that _cancel_remaining iterates,
        # and re-touching them would force an illegal async lazy-load.
        await db.refresh(build, ["status"])
        if cancelled or build.status == "cancelled":
            final_status = "cancelled"
        elif build_failed:
            final_status = "failed"
        else:
            final_status = "success"

        if final_status == "cancelled":
            await _cancel_remaining(db, build)

        build.status = final_status
        build.finished_at = datetime.now(timezone.utc)
        await db.commit()
```

Then add this module-level helper (e.g. just after `_run_build_stages`):

```python
async def _cancel_remaining(db: AsyncSession, build: Build) -> None:
    """Flip any not-yet-terminal stages/steps of *build* to 'cancelled' so the
    UI shows a fully terminal pipeline instead of one frozen mid-run. Relies on
    build.stages / stage.steps already being eager-loaded by the caller."""
    _ACTIVE = ("pending", "running")
    now = datetime.now(timezone.utc)
    for stage in build.stages:
        if stage.status in _ACTIVE:
            stage.status = "cancelled"
            if stage.finished_at is None:
                stage.finished_at = now
        for step in stage.steps:
            if step.status in _ACTIVE:
                step.status = "cancelled"
                if step.finished_at is None:
                    step.finished_at = now
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -k executor -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Run the existing executor gate test for no regression**

Run: `cd backend && python -m pytest tests/test_executor_start_gate.py -v`
Expected: PASS (the start gate still leaves a blocked build `pending`; the new refresh logic runs only after the gate).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/build_executor.py backend/tests/test_build_cancellation.py
git commit -m "fix(builds): executor stops on cancel and finalizes stages/steps cancelled"
```

---

### Task 5: Server-side gate loops bail on the cancel flag

`wait_webhook` / `wait_input` poll Redis for up to their timeout (24h for input) without ever checking build status. Make them check the cancel flag each tick.

**Files:**
- Modify: `backend/app/services/step_actions/wait.py` (both `execute` loops, ~lines 135 and 246)
- Test: `backend/tests/test_build_cancellation.py` (append)

**Interfaces:**
- Consumes: `build_cancel_requested(redis_client, build_id)` from Task 1.

- [ ] **Step 1: Write the failing gate tests**

Append to `backend/tests/test_build_cancellation.py`:

```python
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
```

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -k wait -v`
Expected: FAIL — handlers ignore the flag and block on the poll loop until the (5s) timeout, returning `status="failed"` ("timeout"), not `"cancelled"`. (Test would hang up to 5s then fail on the assert.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -k wait -v`
Expected: FAIL (`assert ... == "cancelled"` — got `"failed"`).

- [ ] **Step 3: Add the cancel check to both loops**

In `backend/app/services/step_actions/wait.py`, add the import near the top (after the existing imports from `app.services.in_app_notifications`):

```python
from app.services.agent_dispatcher import build_cancel_requested
```

In `WaitWebhookHandler.execute`, as the **first statements inside** `while elapsed < timeout:` (line 135):

```python
            if await build_cancel_requested(redis_client, ctx.build_id):
                yield LogLine(stream="system", content="Build cancelled.\n")
                yield StepResult(exit_code=1, status="cancelled")
                return
```

Add the identical block as the first statements inside `WaitInputHandler.execute`'s `while elapsed < timeout:` (line 246).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_build_cancellation.py -k wait -v`
Expected: PASS (2 passed), returning immediately on the first tick.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/step_actions/wait.py backend/tests/test_build_cancellation.py
git commit -m "fix(builds): wait_webhook/wait_input gates bail promptly on cancel"
```

---

### Task 6: Agent kills the whole process tree on cancel

`exec.CommandContext` SIGKILLs only the `/bin/sh -c` (or `cmd.exe /C`) child. Put the shell and its descendants in one group and kill the group.

**Files:**
- Create: `agent/internal/executor/process_unix.go`
- Create: `agent/internal/executor/process_windows.go`
- Modify: `agent/internal/executor/local.go` (call `configureProcessGroup` in `Run` and `runAiAgent`)
- Test: `agent/internal/executor/process_cancel_unix_test.go` (new, Unix-only)

**Interfaces:**
- Produces: `configureProcessGroup(cmd *exec.Cmd)` — sets the OS-specific process-group attribute, a tree-killing `cmd.Cancel`, and a bounded `cmd.WaitDelay`. Must be called after the `*exec.Cmd` is built and before `cmd.Start()`.

- [ ] **Step 1: Write the failing Unix test**

Create `agent/internal/executor/process_cancel_unix_test.go`:

```go
//go:build !windows

package executor

import (
	"context"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// TestRunCancelKillsProcessTree verifies that cancelling a step kills not just
// the shell but the whole process group — a backgrounded grandchild must die.
func TestRunCancelKillsProcessTree(t *testing.T) {
	dir := t.TempDir()
	pidFile := filepath.Join(dir, "child.pid")

	// Background a long sleep (a grandchild of the agent), record its PID,
	// then wait so the shell stays alive until cancelled.
	command := "sleep 30 & echo $! > " + pidFile + "; wait"

	l := NewLocal(Options{Workdir: dir, Capacity: 1})
	ctx, cancel := context.WithCancel(context.Background())
	logs := make(chan LogLine, 64)
	go func() {
		for range logs {
		}
	}()

	done := make(chan Result, 1)
	go func() {
		done <- l.Run(ctx, Step{
			StepID: "s1", BuildID: "b1", StepType: "run", Command: command,
		}, logs)
	}()

	pid := waitForPID(t, pidFile)
	cancel()

	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Run did not return within 10s of cancel")
	}

	if processAlive(pid, 3*time.Second) {
		t.Fatalf("grandchild process %d survived cancellation", pid)
	}
}

func waitForPID(t *testing.T, pidFile string) int {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		b, err := os.ReadFile(pidFile)
		if err == nil {
			if s := strings.TrimSpace(string(b)); s != "" {
				pid, perr := strconv.Atoi(s)
				if perr == nil {
					return pid
				}
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("grandchild never recorded its PID")
	return 0
}

// processAlive returns true if pid is still alive at the end of the window.
func processAlive(pid int, within time.Duration) bool {
	deadline := time.Now().Add(within)
	for {
		// signal 0 probes existence without delivering a signal.
		if err := syscall.Kill(pid, 0); err != nil {
			return false // ESRCH: gone
		}
		if time.Now().After(deadline) {
			return true
		}
		time.Sleep(50 * time.Millisecond)
	}
}
```

Run: `cd agent && go test ./internal/executor/ -run TestRunCancelKillsProcessTree -v`
Expected: FAIL — the orphaned `sleep` survives (only the shell was killed).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd agent && go test ./internal/executor/ -run TestRunCancelKillsProcessTree -v`
Expected: FAIL with "grandchild process N survived cancellation". (On a Windows dev box this file is excluded by the build tag; run it in WSL/Linux CI.)

- [ ] **Step 3: Add the platform-specific helpers**

Create `agent/internal/executor/process_unix.go`:

```go
//go:build !windows

package executor

import (
	"os/exec"
	"syscall"
	"time"
)

// configureProcessGroup puts the command (and every descendant) into a new
// process group, then kills the whole group on context cancel. Without this,
// exec.CommandContext SIGKILLs only the direct child (the shell), leaving
// grandchildren like `docker build` / `npm` running.
func configureProcessGroup(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		// Negative PID targets the whole process group.
		return syscall.Kill(-cmd.Process.Pid, syscall.SIGKILL)
	}
	// Bound the wait so a process ignoring SIGKILL can't wedge cmd.Wait().
	cmd.WaitDelay = 5 * time.Second
}
```

Create `agent/internal/executor/process_windows.go`:

```go
//go:build windows

package executor

import (
	"os/exec"
	"strconv"
	"time"
)

// configureProcessGroup kills the command's whole process tree on cancel.
// Windows has no process groups like POSIX; `taskkill /T /F` terminates the
// process and all its children, which is the dependency-free equivalent.
func configureProcessGroup(cmd *exec.Cmd) {
	cmd.Cancel = func() error {
		if cmd.Process == nil {
			return nil
		}
		kill := exec.Command(
			"taskkill", "/T", "/F", "/PID", strconv.Itoa(cmd.Process.Pid),
		)
		return kill.Run()
	}
	cmd.WaitDelay = 5 * time.Second
}
```

- [ ] **Step 4: Call the helper before starting each command**

In `agent/internal/executor/local.go`:

In `Run`, immediately after `cmd := buildCommand(ctx, command)` (line 106), add:

```go
	configureProcessGroup(cmd)
```

In `runAiAgent`, immediately after `cmd := exec.CommandContext(ctx, "pi", args...)` (line 693), add:

```go
	configureProcessGroup(cmd)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd agent && go test ./internal/executor/ -run TestRunCancelKillsProcessTree -v`
Expected: PASS (the grandchild is gone after cancel). On Windows, verify the build instead: `cd agent && go vet ./...`.

- [ ] **Step 6: Verify both OS builds compile**

Run:
```bash
cd agent && go build ./... && GOOS=windows go build ./... && GOOS=linux go build ./...
```
Expected: all succeed (the unix/windows files compile under their respective tags).

- [ ] **Step 7: Commit**

```bash
git add agent/internal/executor/process_unix.go agent/internal/executor/process_windows.go agent/internal/executor/local.go agent/internal/executor/process_cancel_unix_test.go
git commit -m "fix(agent): kill the whole process tree when a step is cancelled"
```

---

### Task 7: Full regression sweep

**Files:** none (verification only)

- [ ] **Step 1: Run the whole backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS (no regressions; new `test_build_cancellation.py` and updated `test_pipeline_cascade_delete.py` green).

- [ ] **Step 2: Run the whole agent suite**

Run: `cd agent && go test ./...`
Expected: PASS. (On Linux/WSL the process-tree test runs; on Windows it is skipped by build tag but everything compiles and the rest runs.)

- [ ] **Step 3: Manual smoke (optional, requires a live stack + agent)**

Start a build with a long step (e.g. `run: sleep 120`), cancel it from the UI, and confirm: the build flips to `cancelled` within ~1–2s, the agent's `sleep` process is gone, no further steps start, and the pipeline shows all remaining stages/steps `cancelled`. Repeat with a `wait_input` gate to confirm it cancels without waiting for approval.

---

## Notes for the implementer

- **Why DB refresh, not the in-memory attribute:** the worker session uses `expire_on_commit=False`, so `build.status` set once at load never changes on its own. `await db.refresh(build, ["status"])` re-issues `SELECT status` and (because it names only one column) leaves the eager-loaded `stages`/`steps` collections intact — important, since lazy-loading them later in an async session would raise.
- **Ordering invariant the design relies on:** every cancel path commits `build.status="cancelled"` *before* signalling agents/gates. So a boundary refresh always sees the cancel; the executor never needs to infer cancellation from a step's returned status.
- **Don't** reintroduce a bare `notify_agents_of_cancel` call in the cancel endpoint or cascade-delete — both must go through `signal_build_cancel` so the gate flag is always raised.
