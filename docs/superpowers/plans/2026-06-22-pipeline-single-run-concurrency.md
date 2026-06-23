# Single-Run Pipeline Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee a pipeline never runs two builds at once — new triggers queue and coalesce (latest wins) into a single waiting run.

**Architecture:** Two Postgres partial unique indexes on `builds` (`≤1 running` and `≤1 pending` per pipeline) make the invariants physically enforceable. A start gate in the build executor flips `pending→running` only when no sibling is running; a shared `create_or_coalesce_build` helper funnels all four trigger paths so a burst of triggers collapses to one queued run; a dispatcher filter avoids dispatching builds for busy pipelines.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy (async) / Alembic / Celery / pytest. Backend-only — no frontend changes.

## Global Constraints

- **Backend tests:** pytest with `asyncio_mode = "auto"`; run from `backend/` using the venv: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest <args>` (system `python` has no pytest). `pythonpath = ["."]`, `testpaths = ["tests"]`.
- **`@compiles(ARRAY, "sqlite")` MUST return `"TEXTARRAY"`** in every new test harness — it matches `tests/test_build_retry.py`. `@compiles` is process-global and last-write-wins across collected test modules; a divergent value breaks that suite when run together. Always also map `@compiles(UUID,"sqlite")→"CHAR(32)"` and `@compiles(JSONB,"sqlite")=@compiles(PG_JSON,"sqlite")→"JSON"`.
- **Partial indexes in tests:** SQLite supports `CREATE UNIQUE INDEX … WHERE …`. Index-dependent tests create the two concurrency indexes on the test engine via the shared helper from Task 1; logic-only tests don't need them.
- **Invariants (the whole point):** at most one `running` and at most one `pending` build per `pipeline_id`. Concurrency key is `pipeline_id` (branch-agnostic).
- **Coalesce semantics:** when a pending build already exists for the pipeline, update its `commit_sha`, `branch`, `params_json`, `trigger_type`, `triggered_by` (latest wins); do NOT recompile stages/steps; do NOT re-enqueue.
- **Helper transaction contract** (`create_or_coalesce_build`): returns `(build, created)`. `created=True` → build is **flushed, not committed** (caller compiles stages then commits). `created=False` → already **committed** (caller does nothing further: no compile, no enqueue).
- **Migrations are self-contained** (no app imports). Current head is `020`; this is `021`.
- **Commits:** end every commit message body with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Work on branch `feature/pipeline-single-run-concurrency` (already created).

---

### Task 1: Migration 021 — partial unique indexes + duplicate reconciliation

**Files:**
- Create: `backend/alembic/versions/021_pipeline_run_concurrency.py`
- Create: `backend/tests/_concurrency.py` (shared test helper: creates the two partial indexes on a connection)
- Test: `backend/tests/test_pipeline_concurrency_indexes.py`

**Interfaces:**
- Produces: migration `021` (down_revision `020`); test helper `async def create_concurrency_indexes(conn) -> None` creating indexes `uq_one_running_build_per_pipeline` and `uq_one_pending_build_per_pipeline`.

- [ ] **Step 1: Write the shared test helper**

Create `backend/tests/_concurrency.py`:

```python
"""Shared test helper: create the two pipeline-concurrency partial unique
indexes on a SQLite test connection. SQLite supports partial indexes, so this
mirrors what migration 021 creates on Postgres."""


async def create_concurrency_indexes(conn) -> None:
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_running_build_per_pipeline "
        "ON builds (pipeline_id) WHERE status = 'running'"
    )
    await conn.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_one_pending_build_per_pipeline "
        "ON builds (pipeline_id) WHERE status = 'pending'"
    )
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/test_pipeline_concurrency_indexes.py`:

```python
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
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_pipeline_concurrency_indexes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests._concurrency'` until Step 1's file exists; once it does, the index tests fail only if the helper is wrong. (After Step 1 the tests should pass — the indexes are created by the fixture. This task's "implementation" is the migration; the test validates the index semantics the migration will create on Postgres.)

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/021_pipeline_run_concurrency.py`:

```python
"""Enforce single-run pipeline concurrency via partial unique indexes

Revision ID: 021
Revises: 020
Create Date: 2026-06-22

A pipeline must never run two builds at once. Two partial unique indexes make
that physically enforceable: at most one `running` and at most one `pending`
build per pipeline. Pre-existing duplicates (which would make the unique index
creation fail) are reconciled first by cancelling all but the most recent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _reconcile_duplicates(status: str) -> None:
    """Cancel all but the most recent build of `status` per pipeline, so the
    partial unique index can be created. No-op in a healthy system."""
    op.execute(
        sa.text(
            """
            UPDATE builds
            SET status = 'cancelled'
            WHERE status = :status
              AND id NOT IN (
                SELECT DISTINCT ON (pipeline_id) id
                FROM builds
                WHERE status = :status
                ORDER BY pipeline_id, created_at DESC
              )
            """
        ).bindparams(status=status)
    )


def upgrade() -> None:
    _reconcile_duplicates("running")
    _reconcile_duplicates("pending")
    op.create_index(
        "uq_one_running_build_per_pipeline", "builds", ["pipeline_id"],
        unique=True, postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_one_pending_build_per_pipeline", "builds", ["pipeline_id"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_pending_build_per_pipeline", table_name="builds")
    op.drop_index("uq_one_running_build_per_pipeline", table_name="builds")
```

- [ ] **Step 5: Add a reconcile-semantics test**

Append to `backend/tests/test_pipeline_concurrency_indexes.py` (the reconcile uses Postgres `DISTINCT ON`, which SQLite lacks, so this test asserts the *intent* with a portable equivalent — keep newest per pipeline, cancel the rest — then confirms the indexes can be created afterward):

```python
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
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_pipeline_concurrency_indexes.py -v`
Expected: PASS (6 tests). Then full suite: `.venv/Scripts/python.exe -m pytest -q` — all pass.

> The real `alembic upgrade head` against Postgres is verified separately as a deploy/CI step; the migration chain can't run on SQLite (earlier migrations use Postgres types).

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/021_pipeline_run_concurrency.py backend/tests/_concurrency.py backend/tests/test_pipeline_concurrency_indexes.py
git commit -m "$(cat <<'EOF'
feat(builds): migration 021 — partial unique indexes for single-run pipeline concurrency

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Start-gate helpers (`pipeline_has_running_build`, `try_start_build`)

**Files:**
- Create: `backend/app/services/build_concurrency.py`
- Test: `backend/tests/test_build_concurrency_gate.py`

**Interfaces:**
- Consumes: `Build` model; `tests._concurrency.create_concurrency_indexes`.
- Produces:
  - `async def pipeline_has_running_build(db: AsyncSession, pipeline_id: uuid.UUID, exclude_build_id: uuid.UUID | None = None) -> bool`
  - `async def try_start_build(db: AsyncSession, build: Build) -> bool` — sets `status="running"` + `started_at` and commits; on `IntegrityError` (a sibling is already running) rolls back and returns `False`; else returns `True`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_build_concurrency_gate.py`:

```python
"""Start-gate helpers: detect a running sibling, and atomically claim the
single running slot per pipeline."""

import uuid

import pytest_asyncio
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


def _build(pid, number, status):
    from app.models.build import Build
    return Build(id=uuid.uuid4(), pipeline_id=pid, number=number, status=status,
                 trigger_type="manual")


async def test_pipeline_has_running_build(session_factory):
    from app.services.build_concurrency import pipeline_has_running_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        running = _build(pid, 1, "running")
        db.add(running)
        await db.commit()
        assert await pipeline_has_running_build(db, pid) is True
        # excluding the only running build reports no *other* running build
        assert await pipeline_has_running_build(db, pid, exclude_build_id=running.id) is False
        assert await pipeline_has_running_build(db, uuid.uuid4()) is False


async def test_try_start_build_succeeds_when_idle(session_factory):
    from app.services.build_concurrency import try_start_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        b = _build(pid, 1, "pending")
        db.add(b)
        await db.commit()
        assert await try_start_build(db, b) is True
        assert b.status == "running"
        assert b.started_at is not None


async def test_try_start_build_blocked_when_sibling_running(session_factory):
    from app.services.build_concurrency import try_start_build

    pid = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "running"))
        b = _build(pid, 2, "pending")
        db.add(b)
        await db.commit()
        assert await try_start_build(db, b) is False
        await db.refresh(b)
        assert b.status == "pending"  # left untouched
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_build_concurrency_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.build_concurrency'`.

- [ ] **Step 3: Implement the helpers**

Create `backend/app/services/build_concurrency.py`:

```python
"""Single-run pipeline concurrency: a pipeline never runs two builds at once.

Invariants (enforced by partial unique indexes from migration 021):
  - at most one ``running`` build per pipeline  → serialize
  - at most one ``pending`` build per pipeline  → coalesce (latest wins)

This module holds the helpers the trigger paths and the build executor use to
respect those invariants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.build import Build


async def pipeline_has_running_build(
    db: AsyncSession,
    pipeline_id: uuid.UUID,
    exclude_build_id: uuid.UUID | None = None,
) -> bool:
    """True if the pipeline currently has a ``running`` build (other than
    ``exclude_build_id``)."""
    stmt = select(Build.id).where(
        Build.pipeline_id == pipeline_id,
        Build.status == "running",
    )
    if exclude_build_id is not None:
        stmt = stmt.where(Build.id != exclude_build_id)
    return (await db.scalar(stmt.limit(1))) is not None


async def try_start_build(db: AsyncSession, build: Build) -> bool:
    """Atomically flip ``build`` from pending to running, respecting the
    one-running-per-pipeline index. Returns True if it became running, False if
    the pipeline already has a running build (build is left ``pending``)."""
    build.status = "running"
    build.started_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_build_concurrency_gate.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/build_concurrency.py backend/tests/test_build_concurrency_gate.py
git commit -m "$(cat <<'EOF'
feat(builds): start-gate helpers for single-run pipeline concurrency

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `create_or_coalesce_build` helper

**Files:**
- Modify: `backend/app/services/build_concurrency.py` (add the helper)
- Test: `backend/tests/test_create_or_coalesce.py`

**Interfaces:**
- Consumes: `Build` model.
- Produces:
  ```python
  async def create_or_coalesce_build(
      db, *, pipeline_id, default_branch, branch, commit_sha,
      params, triggered_by, trigger_type,
  ) -> tuple[Build, bool]   # (build, created)
  ```
  Per the Global Constraints transaction contract: `created=True` → flushed-not-committed; `created=False` → committed.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_create_or_coalesce.py`:

```python
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
            commit_sha="aaa", params=None, triggered_by=None, trigger_type="webhook")
        assert c1 is True
        await db.commit()

        second, c2 = await create_or_coalesce_build(
            db, pipeline_id=pid, default_branch="main", branch="main",
            commit_sha="bbb", params={"x": "1"}, triggered_by=None, trigger_type="webhook")
        assert c2 is False                 # coalesced, not created
        assert second.id == first.id       # same build row
        assert second.commit_sha == "bbb"  # latest wins
        assert second.params_json == {"x": "1"}

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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_create_or_coalesce.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_or_coalesce_build'`.

- [ ] **Step 3: Implement the helper**

Add to `backend/app/services/build_concurrency.py`:

```python
async def _find_pending(db: AsyncSession, pipeline_id: uuid.UUID) -> Build | None:
    return await db.scalar(
        select(Build)
        .where(Build.pipeline_id == pipeline_id, Build.status == "pending")
        .limit(1)
    )


async def _coalesce(
    db: AsyncSession, existing: Build, *, default_branch, branch, commit_sha,
    params, triggered_by, trigger_type,
) -> tuple[Build, bool]:
    existing.branch = branch or default_branch
    existing.commit_sha = commit_sha
    existing.params_json = params
    existing.trigger_type = trigger_type
    existing.triggered_by = triggered_by
    await db.commit()
    return existing, False


async def create_or_coalesce_build(
    db: AsyncSession,
    *,
    pipeline_id: uuid.UUID,
    default_branch: str,
    branch: str | None,
    commit_sha: str | None,
    params: dict | None,
    triggered_by: uuid.UUID | None,
    trigger_type: str,
) -> tuple[Build, bool]:
    """Create a pending build for the pipeline, or coalesce into the existing
    pending one (latest wins). Returns ``(build, created)``.

    Contract: ``created=True`` → the build is flushed but NOT committed; the
    caller compiles stages/steps and commits. ``created=False`` → already
    committed; the caller does nothing further (no compile, no enqueue).
    """
    existing = await _find_pending(db, pipeline_id)
    if existing is not None:
        return await _coalesce(
            db, existing, default_branch=default_branch, branch=branch,
            commit_sha=commit_sha, params=params, triggered_by=triggered_by,
            trigger_type=trigger_type,
        )

    max_number = await db.scalar(
        select(func.coalesce(func.max(Build.number), 0)).where(
            Build.pipeline_id == pipeline_id
        )
    )
    build = Build(
        pipeline_id=pipeline_id,
        number=(max_number or 0) + 1,
        branch=branch or default_branch,
        commit_sha=commit_sha,
        status="pending",
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        params_json=params,
    )
    db.add(build)
    try:
        await db.flush()  # fires uq_one_pending_build_per_pipeline on a race
    except IntegrityError:
        await db.rollback()
        existing = await _find_pending(db, pipeline_id)
        if existing is None:  # pragma: no cover - pending vanished post-rollback
            raise
        return await _coalesce(
            db, existing, default_branch=default_branch, branch=branch,
            commit_sha=commit_sha, params=params, triggered_by=triggered_by,
            trigger_type=trigger_type,
        )
    return build, True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_create_or_coalesce.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/build_concurrency.py backend/tests/test_create_or_coalesce.py
git commit -m "$(cat <<'EOF'
feat(builds): create_or_coalesce_build — one queued run per pipeline, latest wins

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Wire the start gate into the build executor

**Files:**
- Modify: `backend/app/services/build_executor.py` (Layer A pre-check in `execute_build`; Layer B transition in `_run_build_stages`)
- Test: `backend/tests/test_executor_start_gate.py`

**Interfaces:**
- Consumes: `pipeline_has_running_build`, `try_start_build` (Task 2).
- Produces: `_run_build_stages` leaves a build `pending` (returns early) when the pipeline already has a running build; `execute_build` skips claiming an agent for such a build.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_executor_start_gate.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_executor_start_gate.py -v`
Expected: FAIL — currently `_run_build_stages` sets `status="running"` unconditionally (and would either flip the blocked build to running or raise from the index), not leave it `pending`.

- [ ] **Step 3: Wire Layer B into `_run_build_stages`**

In `backend/app/services/build_executor.py`, add the import near the top (with the other `app.services` imports):

```python
from app.services.build_concurrency import pipeline_has_running_build, try_start_build
```

In `_run_build_stages`, replace the existing transition block:

```python
        build.status = "running"
        build.started_at = datetime.now(timezone.utc)
        await db.commit()
```

with the guarded transition:

```python
        # Serialize: only start if no sibling of this pipeline is already
        # running. The partial unique index makes this atomic across workers;
        # on conflict the build stays pending and is re-dispatched when the
        # running build finishes.
        if not await try_start_build(db, build):
            return
```

- [ ] **Step 4: Wire Layer A pre-check into `execute_build`**

In `execute_build`, immediately AFTER the existing `if build is None or build.status != "pending":` bail block (the one that releases a pre-claimed agent and returns), add a sibling-running pre-check so we don't bother claiming an agent for a build that can't start yet:

```python
        # Don't claim an agent for a build whose pipeline is already running
        # one. Leave it pending; dispatch_pending_builds will retry it after
        # the running build finishes. (Authoritative guard is try_start_build.)
        if await pipeline_has_running_build(db, build.pipeline_id, exclude_build_id=build.id):
            if claimed_agent_id is not None:
                try:
                    await release_agent(db, claimed_agent_id, build_id)
                except Exception:
                    pass
            await redis_client.aclose()
            return
```

- [ ] **Step 5: Run the test + full suite**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_executor_start_gate.py -v`
Expected: PASS. Then `.venv/Scripts/python.exe -m pytest -q` — all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/build_executor.py backend/tests/test_executor_start_gate.py
git commit -m "$(cat <<'EOF'
feat(builds): start gate — never flip a build to running while its pipeline runs

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Wire manual trigger + retry (`builds.py`) through the helper

**Files:**
- Modify: `backend/app/api/v1/builds.py` (`trigger_build`, `retry_build`)
- Test: `backend/tests/test_trigger_coalesce.py`

**Interfaces:**
- Consumes: `create_or_coalesce_build` (Task 3).
- Produces: `trigger_build`/`retry_build` create-or-coalesce; only on `created=True` do they compile/copy stages, commit, and `run_build.delay`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_trigger_coalesce.py` (mirrors the in-memory harness of `tests/test_build_retry.py`, including its side-effect patching and the `TEXTARRAY` ARRAY mapping):

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_trigger_coalesce.py -v`
Expected: FAIL — current `trigger_build` always inserts a new build; the second insert hits `uq_one_pending_build_per_pipeline` (IntegrityError) or yields two pending builds.

- [ ] **Step 3: Rewire `trigger_build`**

In `backend/app/api/v1/builds.py`, add to the compiler import block:

```python
from app.services.build_concurrency import create_or_coalesce_build
```

Replace the body of `trigger_build` from the `max_number = ...` line through the stage/step creation + `await db.commit()` with the create-or-coalesce flow. Keep the existing 404 / disabled / YAML-validation-400 checks ABOVE it unchanged. The new body (after the validation gate):

```python
    build, created = await create_or_coalesce_build(
        db,
        pipeline_id=pipeline_id,
        default_branch=pipeline.default_branch,
        branch=body.branch,
        commit_sha=body.commit_sha,
        params=body.params,
        triggered_by=current_user.id,
        trigger_type="manual",
    )

    if created:
        if pipeline.yaml_content:
            pipeline_def = parse_yaml_pipeline(pipeline.yaml_content)
            build.runs_on = normalize_runs_on(pipeline_def.get("runs_on"))
            stage_defs = compile_to_build_graph(pipeline_def)
            for sort_order, stage_def in enumerate(stage_defs):
                from app.models.build import Stage, Step
                stage = Stage(
                    build_id=build.id, name=stage_def["name"], status="pending",
                    sort_order=sort_order, artifact_paths=stage_def.get("artifacts"),
                )
                db.add(stage)
                await db.flush()
                for step_order, step_def in enumerate(stage_def.get("steps", [])):
                    step_type = step_def.get("step_type", "run")
                    config = step_def.get("config", {})
                    command = config.get("command") if step_type == "run" else None
                    db.add(Step(
                        stage_id=stage.id, name=step_def.get("name", f"step-{step_order}"),
                        step_type=step_type, command=command,
                        config_json=config if config else None,
                        status="pending", sort_order=step_order,
                    ))
        await db.commit()

    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    if created:
        run_build.delay(str(build.id))

    # Best-effort publish to the global builds:updates channel (unchanged).
    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.in_app_notifications import publish_build_update
    settings = get_settings()
    _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        await publish_build_update(_redis, build)
    except Exception:
        pass
    finally:
        await _redis.aclose()

    return build
```

- [ ] **Step 4: Rewire `retry_build`**

In `retry_build`, replace the `max_number = ...` + `new_build = Build(...)` + `db.flush()` with the helper, then copy frozen stages only when `created`. Keep the 404 and "cannot re-run a running build" checks above unchanged. New body after those checks:

```python
    build, created = await create_or_coalesce_build(
        db,
        pipeline_id=original_build.pipeline_id,
        default_branch=original_build.branch or "main",
        branch=original_build.branch,
        commit_sha=original_build.commit_sha,
        params=original_build.params_json,
        triggered_by=current_user.id,
        trigger_type="retry",
    )

    if created:
        build.runs_on = original_build.runs_on
        from app.models.build import Step
        for stage in original_build.stages:
            new_stage = Stage(
                build_id=build.id, name=stage.name, status="pending",
                sort_order=stage.sort_order, artifact_paths=stage.artifact_paths,
            )
            db.add(new_stage)
            await db.flush()
            for step in stage.steps:
                db.add(Step(
                    stage_id=new_stage.id, name=step.name, step_type=step.step_type,
                    command=step.command, config_json=step.config_json,
                    status="pending", sort_order=step.sort_order,
                ))
        await db.commit()

    await db.refresh(build)

    from app.services.search import index_build
    await index_build(build)

    if created:
        run_build.delay(str(build.id))

    from app.config import get_settings
    import redis.asyncio as aioredis
    from app.services.in_app_notifications import publish_build_update
    settings = get_settings()
    _redis = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        await publish_build_update(_redis, build)
    except Exception:
        pass
    finally:
        await _redis.aclose()

    return build
```

- [ ] **Step 5: Run the test + full suite**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_trigger_coalesce.py tests/test_trigger_validation.py tests/test_build_retry.py -v`
Expected: PASS (incl. the existing trigger-validation and retry tests — the validation-400 gate and retry-routing still hold). Then `.venv/Scripts/python.exe -m pytest -q` — all pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/builds.py backend/tests/test_trigger_coalesce.py
git commit -m "$(cat <<'EOF'
feat(builds): manual trigger + retry coalesce into the pipeline's queued run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Wire the git-webhook path through the helper

**Files:**
- Modify: `backend/app/api/v1/webhooks_git.py` (`_enqueue_matching_builds`)
- Test: `backend/tests/test_webhook_coalesce.py`

**Interfaces:**
- Consumes: `create_or_coalesce_build`.
- Produces: `_enqueue_matching_builds` returns ids of **created** builds only (coalesced builds are not re-enqueued); a failed-validation build is still returned (preserving prior behavior).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_webhook_coalesce.py`:

```python
"""A webhook for a pipeline that already has a queued run coalesces into it."""

import types
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
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Pipeline, Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


GOOD_YAML = "name: p\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"


async def test_webhook_coalesces_repeated_pushes(session_factory):
    from app.api.v1.webhooks_git import _enqueue_matching_builds
    from app.models.build import Build
    from app.models.pipeline import Pipeline

    project_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(Pipeline(id=uuid.uuid4(), project_id=project_id, name="p",
                        default_branch="main", yaml_content=GOOD_YAML, enabled=True,
                        created_by=uuid.uuid4(),
                        source_repo_url="https://example.com/r.git"))
        await db.commit()

        repo = types.SimpleNamespace(project_id=project_id, id=uuid.uuid4(),
                                     repo_url="https://example.com/r.git")
        event1 = types.SimpleNamespace(branch="main", commit_sha="aaa")
        event2 = types.SimpleNamespace(branch="main", commit_sha="bbb")

        ids1 = await _enqueue_matching_builds(db, repo, event1)
        ids2 = await _enqueue_matching_builds(db, repo, event2)

    assert len(ids1) == 1            # first push created a build
    assert ids2 == []                # second push coalesced — nothing new to enqueue
    async with session_factory() as db:
        pending = (await db.execute(select(Build).where(Build.status == "pending"))).scalars().all()
        assert len(pending) == 1
        assert pending[0].commit_sha == "bbb"   # latest wins
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_webhook_coalesce.py -v`
Expected: FAIL — the current loop always creates a build; the second push's insert hits the pending unique index.

- [ ] **Step 3: Rewire `_enqueue_matching_builds`**

In `backend/app/api/v1/webhooks_git.py`, add `create_or_coalesce_build` to the local import inside the function (alongside `compile_to_build_graph`, `normalize_runs_on`, `parse_yaml_pipeline`, `validate_pipeline_definition`). Replace the per-pipeline body (the `max_number` + `build = Build(...)` + flush + the `if pipeline.yaml_content:` validate/compile block + `new_build_ids.append`) with:

```python
        build, created = await create_or_coalesce_build(
            db,
            pipeline_id=pipeline.id,
            default_branch=pipeline.default_branch,
            branch=event.branch,
            commit_sha=event.commit_sha,
            params=None,
            triggered_by=None,
            trigger_type="webhook",
        )
        if not created:
            continue  # coalesced into the existing queued run — already committed

        if pipeline.yaml_content:
            validation_errors = validate_pipeline_definition(pipeline.yaml_content)
            if validation_errors:
                from app.services.build_validation import record_pipeline_validation_failure
                await record_pipeline_validation_failure(db, build, validation_errors)
                await db.commit()
                new_build_ids.append(build.id)  # failed build (enqueue is a harmless no-op)
                continue

            pipeline_def = parse_yaml_pipeline(pipeline.yaml_content)
            build.runs_on = normalize_runs_on(pipeline_def.get("runs_on"))
            stage_defs = compile_to_build_graph(pipeline_def)
            for sort_order, stage_def in enumerate(stage_defs):
                stage = Stage(
                    build_id=build.id, name=stage_def["name"], status="pending",
                    sort_order=sort_order, artifact_paths=stage_def.get("artifacts"),
                )
                db.add(stage)
                await db.flush()
                for step_order, step_def in enumerate(stage_def.get("steps", [])):
                    step_type = step_def.get("step_type", "run")
                    config = step_def.get("config", {})
                    command = config.get("command") if step_type == "run" else None
                    db.add(Step(
                        stage_id=stage.id, name=step_def.get("name", f"step-{step_order}"),
                        step_type=step_type, command=command,
                        config_json=config if config else None,
                        status="pending", sort_order=step_order,
                    ))

        await db.commit()
        new_build_ids.append(build.id)
```

> Note: this commits per pipeline (each matched pipeline is independent), which the helper's contract requires. The trailing `await db.commit()` in `receive_webhook` then only persists the delivery record.

- [ ] **Step 4: Run the test + the existing webhook-validation test + full suite**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_webhook_coalesce.py tests/test_webhook_validation.py -v`
Expected: PASS (the existing invalid-YAML webhook test still returns its one failed build). Then `.venv/Scripts/python.exe -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/webhooks_git.py backend/tests/test_webhook_coalesce.py
git commit -m "$(cat <<'EOF'
feat(webhooks): coalesce repeated pushes into the pipeline's single queued run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Wire the `trigger_pipeline` step through the helper

**Files:**
- Modify: `backend/app/services/step_actions/trigger.py` (`TriggerPipelineHandler.execute`)
- Test: `backend/tests/test_trigger_step_coalesce.py`

**Interfaces:**
- Consumes: `create_or_coalesce_build`.
- Produces: the child build is create-or-coalesced; compile + `run_build.delay` happen only on `created=True`; the `wait` polling works against the (possibly coalesced) build.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_trigger_step_coalesce.py`:

```python
"""trigger_pipeline targeting a pipeline that already has a queued run coalesces
into it (no second pending build)."""

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


GOOD_YAML = "name: c\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"


async def test_trigger_step_coalesces(session_factory, monkeypatch):
    monkeypatch.setattr(
        "app.services.step_actions.trigger.run_build.delay", lambda *a, **k: None)

    from app.models.build import Build
    from app.models.pipeline import Pipeline
    from app.services.build_concurrency import create_or_coalesce_build
    from app.services.step_actions.base import StepContext, StepResult
    from app.services.step_actions.trigger import TriggerPipelineHandler

    target_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(Pipeline(id=target_id, project_id=uuid.uuid4(), name="c",
                        default_branch="main", yaml_content=GOOD_YAML, enabled=True,
                        created_by=uuid.uuid4()))
        await db.commit()

        # Pre-existing queued run for the target.
        await create_or_coalesce_build(
            db, pipeline_id=target_id, default_branch="main", branch="main",
            commit_sha="aaa", params=None, triggered_by=None, trigger_type="manual")
        await db.commit()

        ctx = StepContext(build_id=uuid.uuid4(), step_id=uuid.uuid4(),
                          step_name="trigger", stage_name="deploy",
                          pipeline_id=uuid.uuid4(), project_id=uuid.uuid4())
        handler = TriggerPipelineHandler()
        results = []
        async for item in handler.execute(
            {"pipeline": str(target_id), "wait": False}, ctx, db):
            results.append(item)

        final = [r for r in results if isinstance(r, StepResult)][-1]
        assert final.status == "success"   # step still succeeds (run is queued)
        count = await db.scalar(select(func.count()).select_from(Build).where(
            Build.pipeline_id == target_id, Build.status == "pending"))
        assert count == 1                  # coalesced — still one pending
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_trigger_step_coalesce.py -v`
Expected: FAIL — current handler always inserts a child build; the insert hits the pending unique index.

- [ ] **Step 3: Rewire the handler**

In `backend/app/services/step_actions/trigger.py`, add to the top-level import:

```python
from app.services.build_concurrency import create_or_coalesce_build
```

Replace the child-build creation block (the `max_number` + `child_build = Build(...)` + `db.add` + `db.flush`) and the subsequent `if target.yaml_content:` compile block so creation/compile/enqueue happen only when `created`. The validation-failure branch (from the YAML-validation feature) stays as-is in the `created` path. New shape:

```python
        child_build, created = await create_or_coalesce_build(
            db,
            pipeline_id=target.id,
            default_branch=target.default_branch,
            branch=branch,
            commit_sha=None,
            params=params if params else None,
            triggered_by=None,
            trigger_type="pipeline",
        )

        if created and target.yaml_content:
            validation_errors = validate_pipeline_definition(target.yaml_content)
            if validation_errors:
                from app.services.build_validation import (
                    format_validation_errors, record_pipeline_validation_failure,
                )
                await record_pipeline_validation_failure(db, child_build, validation_errors)
                await db.commit()
                detail = format_validation_errors(validation_errors)
                yield LogLine(stream="stderr", content=(
                    f"Error: target pipeline '{target.name}' has invalid YAML:\n{detail}\n"))
                yield StepResult(exit_code=1, status="failed",
                                 error=f"Target pipeline '{target.name}' has invalid YAML")
                return

            pipeline_def = parse_yaml_pipeline(target.yaml_content)
            stage_defs = compile_to_build_graph(pipeline_def)
            for sort_order, stage_def in enumerate(stage_defs):
                stage = Stage(build_id=child_build.id, name=stage_def["name"],
                              status="pending", sort_order=sort_order)
                db.add(stage)
                await db.flush()
                for step_order, step_def in enumerate(stage_def.get("steps", [])):
                    step_type = step_def.get("step_type", "run")
                    step_config = step_def.get("config", {})
                    command = step_config.get("command") if step_type == "run" else None
                    db.add(Step(stage_id=stage.id,
                                name=step_def.get("name", f"step-{step_order}"),
                                step_type=step_type, command=command,
                                config_json=step_config if step_config else None,
                                status="pending", sort_order=step_order))

        if created:
            await db.commit()
        await db.refresh(child_build)

        yield LogLine(stream="stdout", content=(
            f"Triggered pipeline '{target.name}' — build #{child_build.number} "
            f"({child_build.id})\n"))

        if created:
            run_build.delay(str(child_build.id))
```

The existing `if not wait:` early-success block and the `while elapsed < timeout:` polling loop below this stay UNCHANGED — they operate on `child_build` (which, when coalesced, is the existing queued build).

- [ ] **Step 4: Run the test + the existing trigger-step test + full suite**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_trigger_step_coalesce.py tests/test_trigger_step_validation.py -v`
Expected: PASS (the existing invalid-target-YAML step test still fails the step with a recorded child). Then `.venv/Scripts/python.exe -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/step_actions/trigger.py backend/tests/test_trigger_step_coalesce.py
git commit -m "$(cat <<'EOF'
feat(trigger_pipeline): coalesce child trigger into the target's queued run

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Dispatcher filter — skip pipelines with a running build

**Files:**
- Modify: `backend/app/services/agent_dispatcher.py` (`dispatch_pending_builds`, `dispatch_single_build`)
- Test: `backend/tests/test_dispatch_concurrency_filter.py`

**Interfaces:**
- Consumes: `pipeline_has_running_build` (Task 2); `Build` model.
- Produces:
  - `def dispatchable_pending_builds_stmt(limit: int = 20)` in `build_concurrency.py` — returns a SQLAlchemy `Select` for pending builds whose pipeline has NO running build, oldest first. (A plain function returning a statement, so it's unit-testable without running the whole dispatcher.)
  - `dispatch_pending_builds` uses that statement and dispatches at most one build per pipeline per pass; `dispatch_single_build` returns `False` for a build whose pipeline is already running.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_dispatch_concurrency_filter.py`:

```python
"""dispatch_pending_builds skips pending builds whose pipeline already has a
running build."""

import types
import uuid

import pytest_asyncio
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
    from app.models.agent import Agent
    from app.models.build import Build

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Agent, Build):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture(autouse=True)
def _patch(monkeypatch):
    async def _no_maint(db):
        return False
    monkeypatch.setattr("app.api.v1.system.is_maintenance_mode", _no_maint)


def _build(pid, number, status, created_at):
    from app.models.build import Build
    return Build(id=uuid.uuid4(), pipeline_id=pid, number=number, status=status,
                 trigger_type="webhook", created_at=created_at)


async def test_dispatchable_stmt_excludes_busy_pipelines(session_factory):
    """The candidate query returns the idle pipeline's pending build and omits
    the busy pipeline's pending build (its sibling is running)."""
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select
    from app.services.build_concurrency import dispatchable_pending_builds_stmt

    pid_busy, pid_idle = uuid.uuid4(), uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    idle_pending_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid_busy, 1, "running", base))
        db.add(_build(pid_busy, 2, "pending", base + timedelta(minutes=1)))
        idle = _build(pid_idle, 1, "pending", base + timedelta(minutes=2))
        idle.id = idle_pending_id
        db.add(idle)
        await db.commit()

        rows = (await db.execute(dispatchable_pending_builds_stmt())).scalars().all()
        ids = {b.id for b in rows}
        assert idle_pending_id in ids                      # idle pipeline dispatchable
        assert all(b.pipeline_id != pid_busy for b in rows)  # busy pipeline excluded


async def test_dispatch_runs_clean_with_no_agents(session_factory):
    """Sanity: the rewired dispatcher runs without error when no agents exist."""
    from datetime import datetime, timezone
    from app.services import agent_dispatcher

    async with session_factory() as db:
        db.add(_build(uuid.uuid4(), 1, "pending", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        await db.commit()
    await agent_dispatcher.dispatch_pending_builds(session_factory=session_factory)


async def test_dispatch_single_build_blocked_when_pipeline_running(session_factory, monkeypatch):
    """dispatch_single_build refuses a build whose pipeline already runs one."""
    from datetime import datetime, timedelta, timezone
    from app.services import agent_dispatcher

    # dispatch_single_build uses the module-global async_session; point it at ours.
    monkeypatch.setattr("app.database.async_session", session_factory, raising=False)

    pid = uuid.uuid4()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    blocked_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(_build(pid, 1, "running", base))
        b = _build(pid, 2, "pending", base + timedelta(minutes=1))
        b.id = blocked_id
        db.add(b)
        await db.commit()

    assert await agent_dispatcher.dispatch_single_build(blocked_id) is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_dispatch_concurrency_filter.py -v`
Expected: FAIL — `ImportError: cannot import name 'dispatchable_pending_builds_stmt'` (and `dispatch_single_build` does not yet guard on a running sibling).

- [ ] **Step 3a: Add the candidate-query helper**

Add to `backend/app/services/build_concurrency.py`:

```python
from sqlalchemy.orm import aliased


def dispatchable_pending_builds_stmt(limit: int = 20):
    """Select pending builds whose pipeline has NO running build, oldest first.
    Used by the dispatcher so it never starts a build for a busy pipeline."""
    running_sibling = aliased(Build)
    return (
        select(Build)
        .where(
            Build.status == "pending",
            ~select(running_sibling.id)
            .where(
                running_sibling.pipeline_id == Build.pipeline_id,
                running_sibling.status == "running",
            )
            .exists(),
        )
        .order_by(Build.created_at.asc())
        .limit(limit)
    )
```

- [ ] **Step 3b: Use it in the dispatcher**

In `backend/app/services/agent_dispatcher.py`, in `dispatch_pending_builds`, replace the current pending-builds query + loop preamble:

```python
        result = await db.execute(
            select(Build)
            .where(Build.status == "pending")
            .order_by(Build.created_at.asc())
            .limit(20)
        )
        pending_builds = list(result.scalars())
        if not pending_builds:
            return

        for pending in pending_builds:
```

with the filtered version:

```python
        from app.services.build_concurrency import dispatchable_pending_builds_stmt
        result = await db.execute(dispatchable_pending_builds_stmt())
        pending_builds = list(result.scalars())
        if not pending_builds:
            return

        dispatched_pipelines: set[uuid.UUID] = set()
        for pending in pending_builds:
            if pending.pipeline_id in dispatched_pipelines:
                continue  # already dispatched one build for this pipeline this pass
```

Keep the rest of the loop unchanged, and add `dispatched_pipelines.add(pending.pipeline_id)` immediately after the successful `claim_agent` (right before / after the `run_build.delay` call).

In `dispatch_single_build`, after loading the build and the `build is None or build.status != "pending"` check, add:

```python
        from app.services.build_concurrency import pipeline_has_running_build
        if await pipeline_has_running_build(db, build.pipeline_id, exclude_build_id=build.id):
            return False
```

In `dispatch_single_build`, after loading the build and the `status != "pending"` check, add:

```python
        from app.services.build_concurrency import pipeline_has_running_build
        if await pipeline_has_running_build(db, build.pipeline_id, exclude_build_id=build.id):
            return False
```

- [ ] **Step 4: Run the test + full suite**

Run: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest tests/test_dispatch_concurrency_filter.py tests/test_agent_dispatch.py -v`
Expected: PASS (existing dispatch tests still green). Then `.venv/Scripts/python.exe -m pytest -q` — all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agent_dispatcher.py backend/tests/test_dispatch_concurrency_filter.py
git commit -m "$(cat <<'EOF'
feat(dispatch): skip pending builds whose pipeline already has a running build

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] Full backend suite: `cd /c/Projects/MegooCI/backend && .venv/Scripts/python.exe -m pytest -q` — all green, no warnings.
- [ ] `alembic upgrade head` against a Postgres dev DB applies migration 021 cleanly (and `downgrade -1` reverts it).
- [ ] Manual end-to-end sanity: start a long build for a pipeline; fire the webhook (or click Run) twice — observe exactly one queued (`pending`) build that reflects the latest commit, and it starts only after the running build finishes; confirm no two builds of the pipeline are ever `running` together.
