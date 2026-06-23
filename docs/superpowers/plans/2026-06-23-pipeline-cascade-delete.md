# Pipeline Cascade-Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user delete a pipeline that has builds via a project-style two-step cascade: a plain delete that returns `409` listing dependents, then a forced cascade delete behind a warning.

**Architecture:** The build subtree already cascades at the DB level (migration `015`) except `notification_deliveries.build_id`, which has no `ON DELETE` rule and aborts the cascade. We (1) make that FK cascade, (2) rework the pipeline `DELETE` endpoint to count dependents, refuse with `409` unless `?force=true`, and on force cancel any active build then `db.delete(pipeline)` (DB cascade does the rest), and (3) give the pipeline detail page the same two-step confirm flow the project page uses.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 (async) + Alembic (PostgreSQL in prod, in-memory SQLite for tests), Next.js + React + TypeScript frontend.

## Global Constraints

- Migration head is `021_pipeline_run_concurrency`; the new migration is `022`, `down_revision = "021"`.
- Production DB is PostgreSQL. Tests run against in-memory SQLite using the `@compiles` type shims (UUID→`CHAR(32)`, JSONB/JSON→`JSON`, ARRAY→`TEXTARRAY`) and **must** enable `PRAGMA foreign_keys=ON` so `ON DELETE CASCADE` actually fires.
- Backend tests: `pytest` with `asyncio_mode = "auto"` (bare `async def test_...`, no per-test marker). Run from `backend/`.
- Permission on the pipeline delete endpoint stays `pipelines.manage`.
- The 409 detail string MUST begin with the exact phrase **"Cannot delete pipeline"** — the frontend matches on it (case-insensitive).
- Build "active" statuses are `pending`, `queued`, `running`.
- Frontend file/code references use the existing `useConfirm` dialog and `ApiError.body.detail` shape; mirror `projectsApi.delete` / `handleDeleteProject`.

---

### Task 1: Cascade-delete notification_deliveries with their build

Makes `notification_deliveries.build_id` cascade, so deleting a build (and therefore a pipeline, which cascades to builds) no longer aborts on this FK. Also drops the now-redundant manual delete in the project cascade. Establishes the shared SQLite test harness used by Tasks 1 and 3.

**Files:**
- Create: `backend/tests/test_pipeline_cascade_delete.py`
- Create: `backend/alembic/versions/022_cascade_delete_notification_deliveries.py`
- Modify: `backend/app/models/notification.py:64-66` (FK `ondelete`)
- Modify: `backend/app/api/v1/projects.py` (drop redundant delivery delete + its import)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: the test fixtures `session_factory` and `_seed(...)`, and the autouse `_patch_side_effects` fixture, all reused by Task 3. `_seed` signature and return shape:
  - `async def _seed(sf, *, build_statuses=("success",), with_delivery=False) -> SimpleNamespace` returning `.pipeline_id`, `.build_ids` (list), `.delivery_id` (or `None`), `.user_id`.

- [ ] **Step 1: Write the failing test (create the test file with harness + cascade test)**

Create `backend/tests/test_pipeline_cascade_delete.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py::test_deleting_build_cascades_notification_deliveries -v`
Expected: FAIL — committing the build delete raises an `IntegrityError` (SQLite `FOREIGN KEY constraint failed`), because the delivery FK has no `ON DELETE CASCADE` yet.

- [ ] **Step 3: Add `ON DELETE CASCADE` to the model FK**

In `backend/app/models/notification.py`, change `NotificationDelivery.build_id` (currently lines 64-66) from:

```python
    build_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("builds.id"), nullable=True
    )
```

to:

```python
    build_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("builds.id", ondelete="CASCADE"), nullable=True
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py::test_deleting_build_cascades_notification_deliveries -v`
Expected: PASS.

- [ ] **Step 5: Write the Alembic migration**

Create `backend/alembic/versions/022_cascade_delete_notification_deliveries.py`:

```python
"""Add ON DELETE CASCADE to notification_deliveries.build_id

Revision ID: 022
Revises: 021
Create Date: 2026-06-23

A build's notification deliveries must vanish with the build. Without this,
deleting a pipeline (which cascades to its builds) aborts on the
notification_deliveries.build_id FK, so a pipeline with any notified build
cannot be deleted at all.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "notification_deliveries_build_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "notification_deliveries", type_="foreignkey")
    op.create_foreign_key(
        _FK, "notification_deliveries", "builds",
        ["build_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "notification_deliveries", type_="foreignkey")
    op.create_foreign_key(
        _FK, "notification_deliveries", "builds",
        ["build_id"], ["id"],
    )
```

- [ ] **Step 6: Verify the migration is a clean single head**

Run: `cd backend && python -m alembic heads`
Expected: a single head, `022 (head)`. If two heads appear, the `down_revision` is wrong — fix it to `"021"`.

- [ ] **Step 7: Drop the now-redundant manual delivery delete from the project cascade**

In `backend/app/api/v1/projects.py`, inside the `if build_ids:` block, remove the now-redundant statement (the FK now cascades):

```python
                # Null-out notification delivery refs (nullable FK, no cascade).
                await db.execute(
                    sa_delete(NotificationDelivery).where(
                        NotificationDelivery.build_id.in_(build_ids)
                    )
                )
```

Then remove the now-unused local import line in the same function:

```python
        from app.models.notification import NotificationDelivery
```

- [ ] **Step 8: Verify nothing else references the removed import**

Run: `cd backend && python -c "import app.api.v1.projects"`
Expected: no error (module imports cleanly; `NotificationDelivery` is no longer referenced in that file).

- [ ] **Step 9: Commit**

```bash
git add backend/tests/test_pipeline_cascade_delete.py backend/alembic/versions/022_cascade_delete_notification_deliveries.py backend/app/models/notification.py backend/app/api/v1/projects.py
git commit -m "feat(builds): cascade-delete notification_deliveries with their build"
```

---

### Task 2: Extract a shared `notify_agents_of_cancel` helper

The step-cancel signalling lives in the module-private `_notify_agents_of_cancel` in `builds.py`. Lift it into `agent_dispatcher` (next to `signal_cancel_step`) as a public function so both the single-build cancel and the pipeline force-delete use one implementation.

**Files:**
- Modify: `backend/app/services/agent_dispatcher.py` (add `notify_agents_of_cancel`)
- Modify: `backend/app/api/v1/builds.py:178-220` (call the shared helper; delete the private one)
- Modify: `backend/tests/test_pipeline_cascade_delete.py` (add a test)

**Interfaces:**
- Consumes: `session_factory`, `_seed`, `_patch_side_effects` from Task 1.
- Produces: `async def notify_agents_of_cancel(db: AsyncSession, build_id: uuid.UUID) -> None` in `app.services.agent_dispatcher` — publishes a cancel frame for every `running` step of the build that has an `agent_id`; best-effort (swallows per-step errors). Consumed by Task 3.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_pipeline_cascade_delete.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py::test_notify_agents_of_cancel_signals_running_steps -v`
Expected: FAIL — `AttributeError: module 'app.services.agent_dispatcher' has no attribute 'notify_agents_of_cancel'`.

- [ ] **Step 3: Add the helper to `agent_dispatcher`**

In `backend/app/services/agent_dispatcher.py`, add this function (near `signal_cancel_step`, around line 468). It uses the module's existing `select` import:

```python
async def notify_agents_of_cancel(db: AsyncSession, build_id: uuid.UUID) -> None:
    """Publish cancel frames for every running step of ``build_id`` that has an
    ``agent_id``. Best-effort: per-step errors are swallowed so callers (single
    build cancel, pipeline cascade-delete) never fail on a flaky agent channel."""
    from app.models.build import Stage, Step

    result = await db.execute(
        select(Step)
        .join(Stage, Step.stage_id == Stage.id)
        .where(
            Stage.build_id == build_id,
            Step.status == "running",
            Step.agent_id.isnot(None),
        )
    )
    for step in result.scalars().all():
        try:
            if step.agent_id is not None:
                await signal_cancel_step(step.agent_id, step.id)
        except Exception:
            pass
```

If `AsyncSession` is not already imported at the top of `agent_dispatcher.py`, add:

```python
from sqlalchemy.ext.asyncio import AsyncSession
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py::test_notify_agents_of_cancel_signals_running_steps -v`
Expected: PASS.

- [ ] **Step 5: Point `builds.py` at the shared helper and delete the private one**

In `backend/app/api/v1/builds.py`, change the call in `cancel_build` (line 182) from:

```python
    await _notify_agents_of_cancel(db, build_id)
```

to:

```python
    from app.services.agent_dispatcher import notify_agents_of_cancel
    await notify_agents_of_cancel(db, build_id)
```

Then delete the entire private `_notify_agents_of_cancel` function definition (lines 200-220, from `async def _notify_agents_of_cancel(` through its body).

- [ ] **Step 6: Verify builds.py imports cleanly and the cancel test path is intact**

Run: `cd backend && python -c "import app.api.v1.builds"`
Expected: no error (no remaining reference to `_notify_agents_of_cancel`).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/agent_dispatcher.py backend/app/api/v1/builds.py backend/tests/test_pipeline_cascade_delete.py
git commit -m "refactor(agents): extract shared notify_agents_of_cancel helper"
```

---

### Task 3: Pipeline delete endpoint — 409 gate, force cascade, cancel-then-delete

Rework `delete_pipeline` to mirror `delete_project`: count dependents, refuse with `409` unless `force=true`, and on force cancel any active build (signalling its agents) before cascade-deleting the pipeline.

**Files:**
- Modify: `backend/app/api/v1/pipelines.py:1-22` (imports) and `:156-172` (the `delete_pipeline` handler)
- Modify: `backend/tests/test_pipeline_cascade_delete.py` (add endpoint tests)

**Interfaces:**
- Consumes: `session_factory`, `_seed`, `_patch_side_effects` (Task 1); `notify_agents_of_cancel` (Task 2).
- Produces: `DELETE /api/v1/pipelines/{pipeline_id}?force=<bool>` — `404` if missing; `409` (detail starting "Cannot delete pipeline") when builds/triggers/webhook endpoints exist and `force` is false; otherwise `204` after cascade. Consumed by the frontend in Task 4.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_pipeline_cascade_delete.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py -k "delete or force" -v`
Expected: FAIL — the current `delete_pipeline` has no `force` parameter and never raises `409`/`404` per this contract (e.g. `test_delete_pipeline_with_builds_requires_force` fails because no `409` is raised; `force` tests fail on the unexpected signature/behavior).

- [ ] **Step 3: Rewrite the `delete_pipeline` endpoint**

In `backend/app/api/v1/pipelines.py`, update the top-of-file SQLAlchemy import (line 4) from:

```python
from sqlalchemy import select
```

to:

```python
from sqlalchemy import func, select
```

Then replace the whole `delete_pipeline` handler (lines 156-172) with:

```python
@router.delete("/{pipeline_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_pipeline(
    pipeline_id: uuid.UUID,
    force: bool = Query(
        False,
        description=(
            "When true, cancel any running/queued build and cascade-delete the "
            "pipeline together with all of its builds, logs, artifacts, "
            "triggers, and webhook endpoints."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("pipelines.manage")),
) -> None:
    """Delete a pipeline.

    By default the endpoint refuses (409) when the pipeline still has builds,
    triggers, or webhook endpoints, and returns a human-readable list so the UI
    can warn the user. Pass ``?force=true`` to cancel any active build and
    cascade-delete everything the pipeline owns.
    """
    from app.models.build import Build
    from app.models.trigger import Trigger, WebhookEndpoint

    pipeline = await db.get(Pipeline, pipeline_id)
    if pipeline is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found"
        )

    build_count = await db.scalar(
        select(func.count()).select_from(Build).where(Build.pipeline_id == pipeline_id)
    ) or 0
    trigger_count = await db.scalar(
        select(func.count()).select_from(Trigger).where(Trigger.pipeline_id == pipeline_id)
    ) or 0
    webhook_count = await db.scalar(
        select(func.count())
        .select_from(WebhookEndpoint)
        .where(WebhookEndpoint.pipeline_id == pipeline_id)
    ) or 0

    total_dependents = build_count + trigger_count + webhook_count
    if total_dependents > 0 and not force:
        parts: list[str] = []
        if build_count:
            parts.append(f"{build_count} build(s)")
        if trigger_count:
            parts.append(f"{trigger_count} trigger(s)")
        if webhook_count:
            parts.append(f"{webhook_count} webhook endpoint(s)")
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Cannot delete pipeline: it still has "
                + ", ".join(parts)
                + ". Retry with ?force=true to cascade-delete them."
            ),
        )

    if force and build_count > 0:
        # Cancel any in-flight build first, committing the cancelled status so
        # the local executor bails cleanly between steps (it returns early when
        # build.status != 'pending'), then signal agents running steps to stop.
        from app.services.agent_dispatcher import notify_agents_of_cancel

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
            for build in active_builds:
                await notify_agents_of_cancel(db, build.id)

    await db.delete(pipeline)
    await db.commit()

    from app.services.search import remove_pipeline
    await remove_pipeline(str(pipeline_id))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd backend && python -m pytest tests/test_pipeline_cascade_delete.py -v`
Expected: PASS (all tests in the file, including Task 1 and Task 2 tests).

- [ ] **Step 5: Run the broader backend suite for regressions**

Run: `cd backend && python -m pytest -q`
Expected: PASS (no regressions in build/cancel/dispatch tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/pipelines.py backend/tests/test_pipeline_cascade_delete.py
git commit -m "feat(pipelines): cascade-delete pipeline with builds behind force flag"
```

---

### Task 4: Frontend two-step cascade-delete confirm

Add the `force` option to the pipeline delete API client and restructure the detail page's delete handler into the same two-step flow the project page uses.

**Files:**
- Modify: `frontend/src/lib/api.ts:436-437` (`pipelinesApi.delete`)
- Modify: `frontend/src/app/pipelines/[id]/page.tsx:129-153` (`handleDelete`)

**Interfaces:**
- Consumes: the `DELETE /api/v1/pipelines/{id}?force=<bool>` contract from Task 3 (409 detail starts with "Cannot delete pipeline").
- Produces: user-facing two-step delete; no downstream consumers.

- [ ] **Step 1: Add the `force` option to the API client**

In `frontend/src/lib/api.ts`, change `pipelinesApi.delete` (lines 436-437) from:

```ts
  delete: (id: string) =>
    fetchApi<void>(`/api/v1/pipelines/${id}`, { method: "DELETE" }),
```

to:

```ts
  delete: (id: string, opts?: { force?: boolean }) =>
    fetchApi<void>(
      `/api/v1/pipelines/${id}${opts?.force ? "?force=true" : ""}`,
      { method: "DELETE" },
    ),
```

- [ ] **Step 2: Restructure the detail-page delete handler**

In `frontend/src/app/pipelines/[id]/page.tsx`, replace the entire `handleDelete` function (lines 129-153) with:

```tsx
  async function handleDelete() {
    // First attempt: plain delete. The backend returns 409 with a human-
    // readable detail listing the builds/triggers/webhooks in the way. We
    // catch that, surface the detail, and offer a cascade ("Delete everything")
    // path that retries with ?force=true. Mirrors the project delete flow.
    const ok = await confirm({
      title: "Delete this pipeline?",
      description: (
        <>
          <span className="font-medium text-foreground">
            {pipeline?.name ?? "This pipeline"}
          </span>{" "}
          will be removed. This action cannot be undone.
        </>
      ),
      confirmText: "Delete pipeline",
      cancelText: "Keep",
      tone: "destructive",
    });
    if (!ok) return;

    try {
      await pipelinesApi.delete(id);
      toast.success("Pipeline deleted");
      router.push("/pipelines");
      return;
    } catch (err: unknown) {
      const body = (err as { body?: { detail?: string } } | undefined)?.body;
      const detail = body?.detail;

      const isDependentsConflict =
        typeof detail === "string" &&
        detail.toLowerCase().includes("cannot delete pipeline");

      if (!isDependentsConflict) {
        toast.error(
          detail ||
            (err instanceof Error ? err.message : "Failed to delete pipeline"),
        );
        return;
      }

      const forceOk = await confirm({
        title: "Delete pipeline and all its builds?",
        description: (
          <>
            <p>{detail}</p>
            <p className="mt-2 text-sm">
              Proceeding will permanently remove{" "}
              <span className="font-medium text-foreground">
                all builds, logs, artifacts, triggers, and webhook endpoints
              </span>{" "}
              for this pipeline, then delete the pipeline itself.
            </p>
          </>
        ),
        confirmText: "Delete everything",
        cancelText: "Cancel",
        tone: "destructive",
      });
      if (!forceOk) return;

      try {
        await pipelinesApi.delete(id, { force: true });
        toast.success("Pipeline and its builds deleted");
        router.push("/pipelines");
      } catch (err2: unknown) {
        const body2 = (err2 as { body?: { detail?: string } } | undefined)?.body;
        toast.error(
          body2?.detail ||
            (err2 instanceof Error ? err2.message : "Failed to delete pipeline"),
        );
      }
    }
  }
```

- [ ] **Step 3: Typecheck and lint the frontend**

Run: `cd frontend && npx tsc --noEmit && npm run lint`
Expected: no type errors; lint passes for the two changed files.

- [ ] **Step 4: Manual verification**

Start the app, open a pipeline that has builds, and click **Delete**:
1. First dialog "Delete this pipeline?" → confirm.
2. Because builds exist, the backend returns `409`; the second dialog "Delete pipeline and all its builds?" appears showing the build count.
3. Confirm "Delete everything" → pipeline and its builds are gone, toast "Pipeline and its builds deleted", redirected to `/pipelines`.
4. Deleting a pipeline with **no** builds completes on the first confirm.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts "frontend/src/app/pipelines/[id]/page.tsx"
git commit -m "feat(pipelines-ui): two-step cascade-delete confirm"
```

---

## Self-Review

**1. Spec coverage:**
- Schema migration (root-cause fix) → Task 1 (model FK + migration `022` + project-cascade cleanup). ✓
- Backend endpoint: `force` param, 404, dependent counts (builds/triggers/webhooks), 409 with "Cannot delete pipeline", force cascade, cancel-then-delete, `remove_pipeline` → Task 3. ✓
- Shared agent-cancel helper → Task 2. ✓
- Frontend: `pipelinesApi.delete` force option + two-step `handleDelete` → Task 4. ✓
- Tests: 409 without force, full cascade with force, running-build cancellation, no-builds 204, 404, migration-level delivery cascade → Tasks 1 & 3. ✓

**2. Placeholder scan:** No TBD/TODO/"handle edge cases"/"similar to". All code blocks are complete.

**3. Type/name consistency:** `notify_agents_of_cancel(db, build_id)` is defined in Task 2 and called identically in Task 3 and `builds.py`. `_seed(...)` return fields (`pipeline_id`, `build_ids`, `delivery_id`) are used consistently across Tasks 1–3. The 409 phrase "Cannot delete pipeline" is produced in Task 3 and matched (lowercased) in Task 4. `pipelinesApi.delete(id, { force: true })` matches the Task 1 signature shape used by `projectsApi.delete`.

## Notes / Risks

- Dropping the manual `NotificationDelivery` delete from the project cascade (Task 1, Step 7) is safe only once migration `022` is applied in an environment. It is part of this same change set, so deploys that run migrations stay consistent.
- The full DB cascade is exercised on SQLite via `PRAGMA foreign_keys=ON` + `create_all` (which emits the `ON DELETE CASCADE` DDL from the models). The Alembic migration itself (altering an existing Postgres FK) is verified structurally by `alembic heads`, not by a unit test.
- Cascade-deleted builds/artifacts are not removed from the search index — a pre-existing gap shared with the project cascade, explicitly out of scope.
