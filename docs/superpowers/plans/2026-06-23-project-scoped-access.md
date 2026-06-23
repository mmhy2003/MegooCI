# Project-Scoped Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let admins assign specific projects to non-admin users so each non-admin sees and acts on only their assigned projects — and, transitively, the pipelines, builds, artifacts, git repos, and project-scoped secrets under them.

**Architecture:** Reuse the existing scoped `UserRole` (`scope_type="project"`, `scope_id`, `role_id`) — no new table. A central `app/core/access.py` exposes `accessible_project_ids(user, permission)` returning either an `ALL_PROJECTS` sentinel (admin / any global grant) or the set of project IDs where the user holds that permission via a project-scoped role. List endpoints filter on it; detail/mutate endpoints resolve the resource's project and call the existing `check_scoped_permission`.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2 async / Alembic / Meilisearch; Next.js + TypeScript frontend; pytest (`asyncio_mode=auto`, in-memory SQLite with `@compiles` shims); run backend tests with `./.venv/Scripts/python.exe -m pytest` from `backend/`.

## Global Constraints

- A "project assignment" is a `UserRole` row: `scope_type="project"`, `scope_id=<project_id>`, `role_id ∈ {developer, viewer}`. **No new table.**
- Admin = `is_admin` OR a global `admin` role → unrestricted (`ALL_PROJECTS`). Any **global** grant of a permission also → `ALL_PROJECTS` for that permission.
- Non-admins have **no global role**; capability comes from per-project assignments. A user may have many project-scoped rows, one role per (user, project).
- **List endpoints:** zero accessible projects ⇒ return `[]`, never 403.
- **Detail/mutate endpoints:** inaccessible ⇒ 403 via `check_scoped_permission` (consistent with existing behavior).
- Reuse `effective_permissions`, `effective_scoped_permissions`, `check_scoped_permission` from `app/core/deps.py` (they already apply PAT token scope — it composes for free).
- Assignment API: reject the `admin` role at project scope; re-assigning a project the user already has **replaces** the role (upsert).
- **Container registry is unchanged.** Project hierarchy has **no inheritance**.
- Migration preserves existing users' current visibility (convert global developer/viewer → per-project rows across all current projects, drop the global row).
- Tests use in-memory SQLite. Multi-model tests use the full-metadata pattern from `tests/test_pipeline_cascade_delete.py` (`import app.models` + `Base.metadata.create_all` + `PRAGMA foreign_keys=ON`).
- **When switching an endpoint off `require_permission`, keep its EXISTING user-parameter name** (some endpoints use `_current_user`, some `current_user`). Only change the `Depends(...)` to `get_current_active_user` and reference that same name in the scoped check. Never rename the parameter — the tests call each endpoint by its real keyword name. Wherever a task's prose shows `current_user`/`_current_user` in a `check_scoped_permission(...)` call, substitute the endpoint's actual parameter name.

---

### Task 1: Access core + shared RBAC test helpers

**Files:**
- Create: `backend/app/core/access.py`
- Create: `backend/tests/_rbac.py` (shared seeding helpers, reused by Tasks 2–8)
- Test: `backend/tests/test_access_core.py`

**Interfaces:**
- Produces:
  - `ALL_PROJECTS` — module-level sentinel object.
  - `accessible_project_ids(user: User, permission: str) -> set[uuid.UUID] | ALL_PROJECTS`
  - `async project_id_for_pipeline(db, pipeline_id) -> uuid.UUID | None`
  - `async project_id_for_build(db, build_id) -> uuid.UUID | None`
  - `tests/_rbac.py`: `build_inmemory_factory()`, `make_role(name, perms)`, `make_user(is_admin=False, global_role=None, project_roles=())`, plus `seed_project/pipeline/build` helpers (signatures in Step 3).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_access_core.py`:

```python
import os
import uuid
import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_pipeline, seed_build

DEV_PERMS = ["projects.read", "projects.manage", "pipelines.read", "pipelines.manage",
             "builds.read", "builds.manage", "secrets.read", "secrets.manage", "agents.read"]
VIEW_PERMS = ["projects.read", "pipelines.read", "builds.read", "secrets.read", "agents.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


def test_admin_is_all_projects():
    from app.core.access import accessible_project_ids, ALL_PROJECTS
    user = make_user(is_admin=True)
    assert accessible_project_ids(user, "projects.read") is ALL_PROJECTS


def test_global_grant_is_all_projects():
    from app.core.access import accessible_project_ids, ALL_PROJECTS
    user = make_user(global_role=make_role("developer", DEV_PERMS))
    assert accessible_project_ids(user, "pipelines.read") is ALL_PROJECTS


def test_scoped_developer_read_and_manage():
    from app.core.access import accessible_project_ids
    a, b = uuid.uuid4(), uuid.uuid4()
    dev = make_role("developer", DEV_PERMS)
    view = make_role("viewer", VIEW_PERMS)
    user = make_user(project_roles=[(a, dev), (b, view)])
    assert accessible_project_ids(user, "builds.read") == {a, b}
    assert accessible_project_ids(user, "builds.manage") == {a}   # viewer on b can't manage


def test_zero_assignments_is_empty():
    from app.core.access import accessible_project_ids
    user = make_user()  # no roles at all
    assert accessible_project_ids(user, "projects.read") == set()


async def test_resolvers(sf):
    from app.core.access import project_id_for_pipeline, project_id_for_build
    async with sf() as db:
        pid = await seed_project(db, "P")
        pl = await seed_pipeline(db, pid)
        b = await seed_build(db, pl)
        await db.commit()
    async with sf() as db:
        assert await project_id_for_pipeline(db, pl) == pid
        assert await project_id_for_build(db, b) == pid
        assert await project_id_for_build(db, uuid.uuid4()) is None
```

- [ ] **Step 2: Create the shared test helpers**

Create `backend/tests/_rbac.py`:

```python
"""Shared RBAC test scaffolding: in-memory DB + seeding helpers."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import event
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


async def build_inmemory_factory():
    import app.models  # noqa: F401 — registers all tables on Base.metadata
    from app.models.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _rec):  # pragma: no cover
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def make_role(name: str, permissions: list[str]):
    from app.models.role import Role
    return Role(id=uuid.uuid4(), name=name, permissions=list(permissions))


def make_user(*, is_admin: bool = False, global_role=None, project_roles=()):
    """Transient User with attached UserRole objects (role eager-set)."""
    from app.models.role import UserRole
    from app.models.user import User
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="T",
                is_admin=is_admin, is_active=True)
    user.user_roles = []
    if global_role is not None:
        ur = UserRole(id=uuid.uuid4(), user_id=user.id, role_id=global_role.id,
                      scope_type="global", scope_id=None)
        ur.role = global_role
        user.user_roles.append(ur)
    for scope_id, role in project_roles:
        ur = UserRole(id=uuid.uuid4(), user_id=user.id, role_id=role.id,
                      scope_type="project", scope_id=scope_id)
        ur.role = role
        user.user_roles.append(ur)
    return user


async def seed_project(db, name="P", created_by=None) -> uuid.UUID:
    from app.models.project import Project
    from app.models.user import User
    if created_by is None:
        u = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="creator")
        db.add(u)
        await db.flush()
        created_by = u.id
    p = Project(id=uuid.uuid4(), name=f"{name}-{uuid.uuid4().hex[:6]}",
                slug=f"{name.lower()}-{uuid.uuid4().hex[:6]}", created_by=created_by)
    db.add(p)
    await db.flush()
    return p.id


async def seed_pipeline(db, project_id) -> uuid.UUID:
    from app.models.pipeline import Pipeline
    pl = Pipeline(id=uuid.uuid4(), name="pl", project_id=project_id, created_by=None)
    db.add(pl)
    await db.flush()
    return pl.id


async def seed_build(db, pipeline_id, status="success") -> uuid.UUID:
    from app.models.build import Build
    b = Build(id=uuid.uuid4(), pipeline_id=pipeline_id, number=1, status=status,
              trigger_type="manual")
    db.add(b)
    await db.flush()
    return b.id
```

> Note: `seed_pipeline` passes `created_by=None`; if `Pipeline.created_by` is non-nullable in the model, set it to the project's creator instead. The implementer should check `app/models/pipeline.py` and adjust this one helper accordingly.

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_access_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.core.access'`.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_access_core.py -v`
Expected: import error on `app.core.access`.

- [ ] **Step 4: Implement `app/core/access.py`**

```python
"""Project-scoped access: which projects a user can act on for a permission.

The single source of truth for visibility filtering. Composes with the
permission helpers in app.core.deps (which already apply any active PAT scope).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import effective_permissions, effective_scoped_permissions
from app.models.user import User


class _AllProjects:
    """Sentinel meaning 'every project' (admin or a global permission grant)."""
    _singleton = None

    def __new__(cls):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "ALL_PROJECTS"


ALL_PROJECTS = _AllProjects()


def accessible_project_ids(user: User, permission: str):
    """Projects in which *user* effectively holds *permission*.

    Returns ALL_PROJECTS when the user is admin or holds *permission* globally;
    otherwise the set of project_ids granted via project-scoped roles. Empty set
    when the user has no qualifying assignment.
    """
    global_perms = effective_permissions(user)
    if "admin" in global_perms or permission in global_perms:
        return ALL_PROJECTS

    pids: set[uuid.UUID] = set()
    for ur in user.user_roles:
        if ur.scope_type == "project" and ur.scope_id is not None:
            scoped = effective_scoped_permissions(user, "project", ur.scope_id)
            if permission in scoped:
                pids.add(ur.scope_id)
    return pids


async def project_id_for_pipeline(db: AsyncSession, pipeline_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.pipeline import Pipeline
    return await db.scalar(select(Pipeline.project_id).where(Pipeline.id == pipeline_id))


async def project_id_for_build(db: AsyncSession, build_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.build import Build
    from app.models.pipeline import Pipeline
    return await db.scalar(
        select(Pipeline.project_id)
        .join(Build, Build.pipeline_id == Pipeline.id)
        .where(Build.id == build_id)
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_access_core.py -v`
Expected: PASS (5 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/access.py backend/tests/_rbac.py backend/tests/test_access_core.py
git commit -m "feat(rbac): access core (accessible_project_ids + resolvers) + test helpers"
```

---

### Task 2: Projects endpoints — filtered list, scoped detail, assignment cleanup on delete

**Files:**
- Modify: `backend/app/api/v1/projects.py` (`list_projects`, `get_project`, `delete_project`)
- Test: `backend/tests/test_project_scoped_projects.py`

**Interfaces:**
- Consumes: `accessible_project_ids`, `ALL_PROJECTS` (Task 1); `check_scoped_permission`, `get_current_active_user` (deps).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_project_scoped_projects.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project

DEV = ["projects.read", "projects.manage", "pipelines.read", "builds.read"]
VIEW = ["projects.read", "pipelines.read", "builds.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_projects_filtered_to_assigned(sf):
    from app.api.v1.projects import list_projects
    async with sf() as db:
        a = await seed_project(db, "A")
        b = await seed_project(db, "B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_projects(skip=0, limit=20, db=db, _current_user=user)
    ids = {p.id for p in rows}
    assert ids == {a} and b not in ids


async def test_list_projects_admin_sees_all(sf):
    from app.api.v1.projects import list_projects
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    admin = make_user(is_admin=True)
    async with sf() as db:
        rows = await list_projects(skip=0, limit=20, db=db, _current_user=admin)
    assert {p.id for p in rows} == {a, b}


async def test_list_projects_zero_assignments_empty(sf):
    from app.api.v1.projects import list_projects
    async with sf() as db:
        await seed_project(db, "A"); await db.commit()
    user = make_user()
    async with sf() as db:
        rows = await list_projects(skip=0, limit=20, db=db, _current_user=user)
    assert rows == []


async def test_get_project_inaccessible_403(sf):
    from app.api.v1.projects import get_project
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await get_project(b, db=db, current_user=user)
    assert exc.value.status_code == 403


async def test_delete_project_removes_assignments(sf):
    from app.api.v1.projects import delete_project
    from app.models.role import UserRole
    from sqlalchemy import select
    async with sf() as db:
        a = await seed_project(db, "A"); await db.commit()
    user = make_user(is_admin=True)
    # Seed an assignment row pointing at project a.
    async with sf() as db:
        db.add(UserRole(id=uuid.uuid4(), user_id=uuid.uuid4(),
                        role_id=uuid.uuid4(), scope_type="project", scope_id=a))
        await db.commit()
    async with sf() as db:
        await delete_project(a, force=False, db=db, current_user=user)
    async with sf() as db:
        rows = (await db.execute(
            select(UserRole).where(UserRole.scope_type == "project", UserRole.scope_id == a)
        )).scalars().all()
    assert rows == []
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_projects.py -v`
Expected: FAIL — current `list_projects` uses `require_permission` and returns all; signatures differ (`_current_user`/`current_user`).

- [ ] **Step 2: Run tests to verify they fail**

Run the command above. Expected failures: filtering assertions and the new delete-cleanup assertion.

- [ ] **Step 3: Edit `projects.py`**

`list_projects` — replace the dependency and add filtering:

```python
from app.core.access import accessible_project_ids, ALL_PROJECTS
from app.core.deps import check_scoped_permission, get_current_active_user, require_permission


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Project]:
    pids = accessible_project_ids(_current_user, "projects.read")
    if pids is not ALL_PROJECTS and not pids:
        return []
    query = select(Project).order_by(Project.created_at.desc())
    if pids is not ALL_PROJECTS:
        query = query.where(Project.id.in_(pids))
    result = await db.execute(query.offset(skip).limit(limit))
    return list(result.scalars().all())
```

`get_project` — swap the global dep for the active-user dep so the scoped check governs (it already calls `check_scoped_permission`):

```python
@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
) -> Project:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    check_scoped_permission(current_user, "projects.read", "project", project_id)
    return project
```

`update_project` and `delete_project` — change their dependency from `Depends(require_permission("projects.manage"))` to `Depends(get_current_active_user)` (they already call `check_scoped_permission(..., "projects.manage", ...)` first, so a scoped developer passes and a non-member 403s). `create_project` keeps `Depends(require_permission("projects.manage"))` (global — admin action).

In `delete_project`, after the `await db.delete(project)` / before `await db.commit()`, clean up project-scoped assignments:

```python
    from app.models.role import UserRole
    await db.execute(
        sa_delete(UserRole).where(
            UserRole.scope_type == "project", UserRole.scope_id == project_id
        )
    )
    await db.delete(project)
    await db.commit()
```

(`sa_delete` is already imported in this file.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_projects.py -v`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/projects.py backend/tests/test_project_scoped_projects.py
git commit -m "feat(rbac): scope projects list/detail to assignments; clean up rows on delete"
```

---

### Task 3: Pipelines endpoints — filtered list + scoped detail/mutate

**Files:**
- Modify: `backend/app/api/v1/pipelines.py` (`list_pipelines`, `get_pipeline`, `create_pipeline`, `update_pipeline`, `delete_pipeline`)
- Test: `backend/tests/test_project_scoped_pipelines.py`

**Interfaces:**
- Consumes: `accessible_project_ids`, `ALL_PROJECTS`, `project_id_for_pipeline` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_project_scoped_pipelines.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_pipeline

DEV = ["projects.read", "pipelines.read", "pipelines.manage", "builds.read"]
VIEW = ["projects.read", "pipelines.read", "builds.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_pipelines_filtered(sf):
    from app.api.v1.pipelines import list_pipelines
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pa = await seed_pipeline(db, a); pb = await seed_pipeline(db, b)
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        rows = await list_pipelines(project_id=None, skip=0, limit=20, db=db, _current_user=user)
    assert {p.id for p in rows} == {pa}


async def test_list_pipelines_project_filter_inaccessible_empty(sf):
    from app.api.v1.pipelines import list_pipelines
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await seed_pipeline(db, b); await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        rows = await list_pipelines(project_id=b, skip=0, limit=20, db=db, _current_user=user)
    assert rows == []


async def test_get_pipeline_scoped_403(sf):
    from app.api.v1.pipelines import get_pipeline
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pb = await seed_pipeline(db, b); await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await get_pipeline(pb, db=db, _current_user=user)
    assert exc.value.status_code == 403
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_pipelines.py -v`
Expected: FAIL (current list returns all; `get_pipeline` gates on global `pipelines.read`).

- [ ] **Step 2: Run tests to verify they fail**

Run the command above; expect filtering + 403 failures.

- [ ] **Step 3: Edit `pipelines.py`**

Add imports: `from app.core.access import accessible_project_ids, ALL_PROJECTS, project_id_for_pipeline` and `from app.core.deps import check_scoped_permission, get_current_active_user`.

`list_pipelines`:

```python
@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    project_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Pipeline]:
    pids = accessible_project_ids(_current_user, "pipelines.read")
    if pids is not ALL_PROJECTS and not pids:
        return []
    if project_id is not None and pids is not ALL_PROJECTS and project_id not in pids:
        return []
    query = select(Pipeline).order_by(Pipeline.created_at.desc())
    if project_id is not None:
        query = query.where(Pipeline.project_id == project_id)
    elif pids is not ALL_PROJECTS:
        query = query.where(Pipeline.project_id.in_(pids))
    result = await db.execute(query.offset(skip).limit(limit))
    return list(result.scalars().all())
```

> **Keep each endpoint's EXISTING user-parameter name** — `get_pipeline`, `update_pipeline`, `delete_pipeline` use `_current_user`; `create_pipeline` uses `current_user`. Only change the `Depends(...)` to `get_current_active_user` and reference that same name in the scoped check. Do NOT rename parameters (tests call them by name).

`get_pipeline`: keep `_current_user`, switch its dep to `Depends(get_current_active_user)`; after loading the pipeline (404 if missing) add `check_scoped_permission(_current_user, "pipelines.read", "project", pipeline.project_id)`.

`create_pipeline`: keep `current_user`, switch its dep to `Depends(get_current_active_user)`; after validating `body.project_id` exists, add `check_scoped_permission(current_user, "pipelines.manage", "project", body.project_id)` before creating.

`update_pipeline` / `delete_pipeline` (and the cascade-delete force path): keep `_current_user`, switch its dep to `Depends(get_current_active_user)`; after loading the pipeline, add `check_scoped_permission(_current_user, "pipelines.manage", "project", pipeline.project_id)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_pipelines.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/pipelines.py backend/tests/test_project_scoped_pipelines.py
git commit -m "feat(rbac): scope pipelines list/detail/mutate to assignments"
```

---

### Task 4: Builds + artifacts endpoints — scoped via build→pipeline→project

**Files:**
- Modify: `backend/app/api/v1/builds.py` (`list_builds`, `get_build`, trigger, cancel, retry, delete, logs)
- Modify: `backend/app/api/v1/artifacts.py` (`list_all_artifacts`, `list_artifacts`, `upload_artifact` [user path], signed-url, download)
- Test: `backend/tests/test_project_scoped_builds.py`

**Interfaces:**
- Consumes: `accessible_project_ids`, `ALL_PROJECTS`, `project_id_for_build` (Task 1).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_project_scoped_builds.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_pipeline, seed_build

DEV = ["pipelines.read", "builds.read", "builds.manage"]
VIEW = ["pipelines.read", "builds.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_builds_filtered_by_project(sf):
    from app.api.v1.builds import list_builds
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pa = await seed_pipeline(db, a); pb = await seed_pipeline(db, b)
        ba = await seed_build(db, pa); bb = await seed_build(db, pb)
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        rows = await list_builds(pipeline_id=None, skip=0, limit=20, db=db, _current_user=user)
    assert {x.id for x in rows} == {ba}


async def test_get_build_inaccessible_403(sf):
    from app.api.v1.builds import get_build
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pb = await seed_pipeline(db, b); bb = await seed_build(db, pb)
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await get_build(bb, db=db, _current_user=user)
    assert exc.value.status_code == 403
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_builds.py -v`
Expected: FAIL.

- [ ] **Step 2: Run tests to verify they fail**

Run the command above.

- [ ] **Step 3: Edit `builds.py`**

Add imports: `from app.core.access import accessible_project_ids, ALL_PROJECTS, project_id_for_build` and `from app.core.deps import check_scoped_permission, get_current_active_user`.

`list_builds` — filter by `build→pipeline→project`:

```python
@router.get("", response_model=list[BuildResponse])
async def list_builds(
    pipeline_id: uuid.UUID | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Build]:
    pids = accessible_project_ids(_current_user, "builds.read")
    if pids is not ALL_PROJECTS and not pids:
        return []
    query = select(Build).order_by(Build.created_at.desc())
    if pids is not ALL_PROJECTS:
        query = query.join(Pipeline, Build.pipeline_id == Pipeline.id).where(
            Pipeline.project_id.in_(pids)
        )
    if pipeline_id is not None:
        # If filtering to one pipeline, ensure it's in an accessible project.
        if pids is not ALL_PROJECTS:
            proj = await db.scalar(select(Pipeline.project_id).where(Pipeline.id == pipeline_id))
            if proj is None or proj not in pids:
                return []
        query = query.where(Build.pipeline_id == pipeline_id)
    result = await db.execute(query.offset(skip).limit(limit))
    return list(result.scalars().all())
```

For every other build endpoint (`get_build`, `get_build_logs`, `trigger_build`, `cancel_build`, `retry_build`, `delete_build`): switch the dependency to `Depends(get_current_active_user)` and, after resolving the build/pipeline, enforce scope. Read endpoints use `builds.read`, mutating ones `builds.manage`. Pattern:

```python
    project_id = await project_id_for_build(db, build_id)
    if project_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Build not found")
    check_scoped_permission(current_user, "builds.read", "project", project_id)  # or builds.manage
```

`trigger_build` takes a `pipeline_id` (not build_id): resolve with `project_id_for_pipeline` and check `builds.manage`.

> **Keep each endpoint's EXISTING user-parameter name** (`get_build`, `list_builds`, `get_build_logs`, `delete_build`, `cancel_build` use `_current_user`; `trigger_build`, `retry_build` use `current_user`). Only change the `Depends(...)` to `get_current_active_user` and reference that same name in the scoped check. Do NOT rename parameters — the tests call them by name (`get_build(..., _current_user=user)`).

- [ ] **Step 4: Edit `artifacts.py`**

Add imports: `from app.core.access import accessible_project_ids, ALL_PROJECTS, project_id_for_build` and `from app.core.deps import check_scoped_permission, get_current_active_user`.

- `list_all_artifacts`: switch dep to `Depends(get_current_active_user)`; compute `pids = accessible_project_ids(user, "artifacts.read")`; if empty → `[]`; if not `ALL_PROJECTS`, add `.where(Project.id.in_(pids))` to the existing build→pipeline→project join (the query already joins through to project context — add the project filter; if it doesn't join Project yet, add `.join(Project, Pipeline.project_id == Project.id)`).
- `list_artifacts`, `upload_artifact` (the **user** endpoint), `get_signed_url`, `download_artifact`: switch dep to `Depends(get_current_active_user)`; resolve `project_id = await project_id_for_build(db, build_id)` (or via the artifact's build for `download`/`signed_url`) and `check_scoped_permission(user, "artifacts.read", ...)` (read) / `"artifacts.manage"` (upload).

> **Do not touch agent-token artifact uploads.** The agent uploads via its own auth path (`agent_auth`), not `get_current_user`; that flow is unchanged. Only the user-facing artifact endpoints (those currently using `require_permission("artifacts.*")`) get scoped.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_builds.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/builds.py backend/app/api/v1/artifacts.py backend/tests/test_project_scoped_builds.py
git commit -m "feat(rbac): scope builds + artifacts to assignments via build->pipeline->project"
```

---

### Task 5: Git project repositories — scoped via path project_id

**Files:**
- Modify: `backend/app/api/v1/project_repositories.py` (`list_repositories`, `link_repository`, `update_repository`, `unlink_repository`)
- Test: `backend/tests/test_project_scoped_repos.py`

**Interfaces:**
- Consumes: `check_scoped_permission`, `get_current_active_user`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_project_scoped_repos.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project

VIEW = ["projects.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_repositories_scoped_403(sf):
    from app.api.v1.project_repositories import list_repositories
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await list_repositories(project_id=b, db=db, _current_user=user)
    assert exc.value.status_code == 403


async def test_list_repositories_member_ok(sf):
    from app.api.v1.project_repositories import list_repositories
    async with sf() as db:
        a = await seed_project(db, "A"); await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        rows = await list_repositories(project_id=a, db=db, _current_user=user)
    assert rows == []
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_repos.py -v`
Expected: FAIL (currently gates on global `projects.read` → viewer-on-a has no global perm → 403 on BOTH, including the should-pass case).

- [ ] **Step 2: Run tests to verify they fail**

Run the command above. The member-ok test fails (currently 403 for a scoped user).

- [ ] **Step 3: Edit `project_repositories.py`**

Add `from app.core.deps import check_scoped_permission, get_current_active_user`. For each endpoint, switch the dependency to `Depends(get_current_active_user)` and add a scoped check on the **path** `project_id` (read for GET, manage for mutate), right after `_get_project_or_404`:

```python
@router.get("/", response_model=list[ProjectRepositoryResponse])
async def list_repositories(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[ProjectRepository]:
    await _get_project_or_404(db, project_id)
    check_scoped_permission(_current_user, "projects.read", "project", project_id)
    ...
```

`link_repository` / `update_repository` / `unlink_repository`: same swap, with `check_scoped_permission(current_user, "projects.manage", "project", project_id)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_repos.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/project_repositories.py backend/tests/test_project_scoped_repos.py
git commit -m "feat(rbac): scope project git repositories to assignments"
```

---

### Task 6: Secrets & env vars — scoped by row's project; global rows admin-only

**Files:**
- Modify: `backend/app/api/v1/secrets.py` (all six endpoints)
- Test: `backend/tests/test_project_scoped_secrets.py`

**Interfaces:**
- Consumes: `accessible_project_ids`, `ALL_PROJECTS`; `check_scoped_permission`, `effective_permissions`, `get_current_active_user`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_project_scoped_secrets.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project

DEV = ["secrets.read", "secrets.manage"]


def _seed_secret(db, scope_type, scope_id, name):
    from app.models.secret import Secret
    db.add(Secret(id=uuid.uuid4(), scope_type=scope_type, scope_id=scope_id,
                  name=name, secret_type="text", encrypted_payload=b"x",
                  created_by=uuid.uuid4()))


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_secrets_project_scope_member_sees_only_their_project(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        a = await seed_project(db, "A")
        _seed_secret(db, "project", a, "S_A")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_secrets(scope_type="project", scope_id=a, db=db, _current_user=user)
    assert {s.name for s in rows} == {"S_A"}


async def test_list_secrets_project_scope_nonmember_empty(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        _seed_secret(db, "project", b, "S_B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_secrets(scope_type="project", scope_id=b, db=db, _current_user=user)
    assert rows == []


async def test_list_global_secrets_hidden_from_nonadmin(sf):
    from app.api.v1.secrets import list_secrets
    async with sf() as db:
        a = await seed_project(db, "A")
        _seed_secret(db, "global", None, "S_GLOBAL")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("developer", DEV))])
    async with sf() as db:
        rows = await list_secrets(scope_type="global", scope_id=None, db=db, _current_user=user)
    assert rows == []
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_secrets.py -v`
Expected: FAIL.

- [ ] **Step 2: Run tests to verify they fail**

Run the command above.

- [ ] **Step 3: Edit `secrets.py`**

Add `from app.core.access import accessible_project_ids, ALL_PROJECTS` and `from app.core.deps import check_scoped_permission, effective_permissions, get_current_active_user`.

`list_secrets` / `list_env_vars`: switch dep to `Depends(get_current_active_user)`; enforce per scope:

```python
@router.get("/secrets", response_model=list[SecretResponse])
async def list_secrets(
    scope_type: str = Query(...),
    scope_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_active_user),
) -> list[Secret]:
    if scope_type == "project" and scope_id is not None:
        pids = accessible_project_ids(_current_user, "secrets.read")
        if pids is not ALL_PROJECTS and scope_id not in pids:
            return []
    else:
        # Non-project (e.g. global) scope: require a global secrets.read.
        if "secrets.read" not in effective_permissions(_current_user) \
                and "admin" not in effective_permissions(_current_user):
            return []
    query = select(Secret).where(Secret.scope_type == scope_type)
    if scope_id is not None:
        query = query.where(Secret.scope_id == scope_id)
    else:
        query = query.where(Secret.scope_id.is_(None))
    result = await db.execute(query.order_by(Secret.name))
    return list(result.scalars().all())
```

`create_secret` / `create_env_var`: switch dep to `Depends(get_current_active_user)`; if `body.scope_type == "project"` and `body.scope_id` → `check_scoped_permission(current_user, "secrets.manage", "project", body.scope_id)`; else require global `secrets.manage` (raise 403 if not in `effective_permissions`).

`update_*` / `delete_*` (six endpoints): switch dep to `Depends(get_current_active_user)`; after loading the row, if its `scope_type == "project"` → `check_scoped_permission(current_user, "secrets.manage", "project", row.scope_id)`; else require global `secrets.manage`.

> Apply the identical pattern to the three env-var endpoints (`list_env_vars`, `create_env_var`, `update_env_var`, `delete_env_var`) using `EnvVar`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_project_scoped_secrets.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/secrets.py backend/tests/test_project_scoped_secrets.py
git commit -m "feat(rbac): scope secrets/env to project assignments; global rows admin-only"
```

---

### Task 7: Assignment API hardening + project members endpoint

**Files:**
- Modify: `backend/app/api/v1/users.py` (`assign_role`, `_user_to_detail`)
- Modify: `backend/app/api/v1/projects.py` (new `GET /{project_id}/members`)
- Modify: `backend/app/schemas/project.py` (add `ProjectMemberResponse`)
- Test: `backend/tests/test_assignment_api.py`

**Interfaces:**
- Consumes: existing `UserRoleAssign` (`role_id`, `scope_type="global"`, `scope_id=None`).
- Produces: `GET /projects/{id}/members` → `list[ProjectMemberResponse]` with `{user_id, email, name, role_name}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_assignment_api.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_project


async def _seed_role(db, name, perms):
    from app.models.role import Role
    r = Role(id=uuid.uuid4(), name=name, permissions=perms, is_system=True)
    db.add(r); await db.flush(); return r.id


async def _seed_user(db, email="u@e.com"):
    from app.models.user import User
    u = User(id=uuid.uuid4(), email=email, name="U", is_active=True)
    db.add(u); await db.flush(); return u.id


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_assign_project_role_rejects_missing_project(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    async with sf() as db:
        uid = await _seed_user(db)
        dev = await _seed_role(db, "developer", ["pipelines.read"])
        await db.commit()
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await assign_role(uid, UserRoleAssign(role_id=dev, scope_type="project",
                              scope_id=uuid.uuid4()), db=db, _current_user=None)
    assert exc.value.status_code in (400, 404)


async def test_assign_rejects_admin_at_project_scope(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    async with sf() as db:
        uid = await _seed_user(db)
        admin = await _seed_role(db, "admin", ["admin"])
        pid = await seed_project(db, "A")
        await db.commit()
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await assign_role(uid, UserRoleAssign(role_id=admin, scope_type="project",
                              scope_id=pid), db=db, _current_user=None)
    assert exc.value.status_code == 400


async def test_reassign_project_replaces_role(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    from app.models.role import UserRole
    from sqlalchemy import select
    async with sf() as db:
        uid = await _seed_user(db)
        dev = await _seed_role(db, "developer", ["pipelines.manage"])
        view = await _seed_role(db, "viewer", ["pipelines.read"])
        pid = await seed_project(db, "A")
        await db.commit()
    async with sf() as db:
        await assign_role(uid, UserRoleAssign(role_id=dev, scope_type="project", scope_id=pid),
                          db=db, _current_user=None)
        await db.commit()
    async with sf() as db:
        await assign_role(uid, UserRoleAssign(role_id=view, scope_type="project", scope_id=pid),
                          db=db, _current_user=None)
        await db.commit()
    async with sf() as db:
        rows = (await db.execute(select(UserRole).where(
            UserRole.user_id == uid, UserRole.scope_type == "project", UserRole.scope_id == pid
        ))).scalars().all()
    assert len(rows) == 1 and rows[0].role_id == view
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_assignment_api.py -v`
Expected: FAIL (no project validation; admin@project allowed; re-assign 409s instead of replacing).

- [ ] **Step 2: Run tests to verify they fail**

Run the command above.

- [ ] **Step 3: Edit `assign_role` in `users.py`**

Replace the body's validation/insert region (after loading the role) with:

```python
    from app.models.project import Project

    # Project-scoped assignment validation.
    if body.scope_type == "project":
        if body.scope_id is None or await db.get(Project, body.scope_id) is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="scope_id must reference an existing project")
        if role.name == "admin":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                detail="The admin role cannot be scoped to a project")
        # One role per (user, project): replace any existing project-scoped row.
        existing_proj = await db.execute(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.scope_type == "project",
                UserRole.scope_id == body.scope_id,
            )
        )
        prior = existing_proj.scalar_one_or_none()
        if prior is not None:
            prior.role_id = body.role_id
            await db.flush()
            await db.refresh(prior)
            return {
                "id": prior.id, "user_id": prior.user_id, "role_id": prior.role_id,
                "scope_type": prior.scope_type, "scope_id": prior.scope_id,
                "role_name": role.name, "created_at": prior.created_at,
            }

    # Exact-duplicate guard (unchanged) for non-project or first-time project rows.
    existing = await db.execute(
        select(UserRole).where(
            UserRole.user_id == user_id, UserRole.role_id == body.role_id,
            UserRole.scope_type == body.scope_type, UserRole.scope_id == body.scope_id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="User already has this role in the specified scope")
    # ... existing insert of new UserRole unchanged ...
```

In `_user_to_detail`, enrich each role with the project name when `scope_type == "project"`. Because `_user_to_detail` is sync, add an async pre-fetch in the callers (`list_users`, `get_user`, `assign_role` response builder) OR change `_user_to_detail` to accept a `project_names: dict[uuid.UUID, str]` map. Simplest: add a `project_names` param defaulting to `{}` and, in `list_users`/`get_user`, build the map with one query:

```python
from app.models.project import Project

async def _project_name_map(db, users) -> dict:
    pids = {ur.scope_id for u in users for ur in u.user_roles
            if ur.scope_type == "project" and ur.scope_id}
    if not pids:
        return {}
    rows = await db.execute(select(Project.id, Project.name).where(Project.id.in_(pids)))
    return {pid: name for pid, name in rows.all()}
```

and include `"project_name": project_names.get(ur.scope_id)` in each role dict (add `project_name: str | None = None` to the role entries in `UserDetailResponse`/`UserRoleInfo` schema).

- [ ] **Step 4: Add `GET /projects/{id}/members`**

In `schemas/project.py`:

```python
class ProjectMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role_name: str
```

In `projects.py`:

```python
@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(require_permission("users.manage")),
) -> list[dict]:
    from app.models.role import Role, UserRole
    from app.models.user import User as UserModel
    rows = await db.execute(
        select(UserModel.id, UserModel.email, UserModel.name, Role.name)
        .join(UserRole, UserRole.user_id == UserModel.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.scope_type == "project", UserRole.scope_id == project_id)
        .order_by(UserModel.email)
    )
    return [{"user_id": uid, "email": email, "name": name, "role_name": rn}
            for uid, email, name, rn in rows.all()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_assignment_api.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/users.py backend/app/api/v1/projects.py backend/app/schemas/project.py backend/app/schemas/users.py backend/tests/test_assignment_api.py
git commit -m "feat(rbac): validate/upsert project assignments; project members endpoint"
```

---

### Task 8: User creation + invite transition (non-admin → no global role)

**Files:**
- Modify: `backend/app/api/v1/users.py` (`create_user`)
- Modify: `backend/app/api/v1/invites.py` (`accept_invite`)
- Modify: `backend/app/schemas/users.py` (`UserCreateRequest`)
- Test: `backend/tests/test_user_creation_transition.py`

**Interfaces:**
- Produces: `create_user` no longer creates a global non-admin `UserRole`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_user_creation_transition.py`:

```python
import os, uuid
import pytest, pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory


async def _seed_role(db, name, perms):
    from app.models.role import Role
    r = Role(id=uuid.uuid4(), name=name, permissions=perms, is_system=True)
    db.add(r); await db.flush(); return r.id


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_create_nonadmin_user_has_no_global_role(sf):
    from app.api.v1.users import create_user
    from app.schemas.users import UserCreateRequest
    from app.models.role import UserRole
    from sqlalchemy import select
    async with sf() as db:
        dev = await _seed_role(db, "developer", ["pipelines.read"])
        await db.commit()
    async with sf() as db:
        await create_user(UserCreateRequest(email="x@e.com", name="X", role_id=dev),
                          db=db, _current_user=None)
        await db.commit()
    async with sf() as db:
        from app.models.user import User
        uid = (await db.execute(select(User.id).where(User.email == "x@e.com"))).scalar_one()
        roles = (await db.execute(select(UserRole).where(UserRole.user_id == uid))).scalars().all()
    assert roles == [], "non-admin must be created with NO global role"


async def test_create_admin_user_keeps_global_admin(sf):
    from app.api.v1.users import create_user
    from app.schemas.users import UserCreateRequest
    from app.models.role import UserRole
    from sqlalchemy import select
    async with sf() as db:
        admin = await _seed_role(db, "admin", ["admin"])
        await db.commit()
    async with sf() as db:
        await create_user(UserCreateRequest(email="a@e.com", name="A", role_id=admin),
                          db=db, _current_user=None)
        await db.commit()
    async with sf() as db:
        from app.models.user import User
        u = (await db.execute(select(User).where(User.email == "a@e.com"))).scalar_one()
        roles = (await db.execute(select(UserRole).where(UserRole.user_id == u.id))).scalars().all()
    assert u.is_admin is True
    assert len(roles) == 1 and roles[0].scope_type == "global"
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_user_creation_transition.py -v`
Expected: FAIL — non-admin currently gets a global role row.

- [ ] **Step 2: Run tests to verify they fail**

Run the command above.

- [ ] **Step 3: Edit `create_user`**

Only assign a global `UserRole` when the chosen role is `admin`; otherwise create the user with no role (projects assigned later):

```python
    user = User(
        email=body.email, name=body.name,
        hashed_password=hash_password(generated_password),
        is_admin=role.name == "admin", is_active=True,
    )
    db.add(user)
    await db.flush()

    if role.name == "admin":
        db.add(UserRole(user_id=user.id, role_id=body.role_id, scope_type="global"))
        await db.flush()
    # Non-admins start with no role; an admin assigns project roles afterward.
```

- [ ] **Step 4: Edit `accept_invite` in `invites.py`**

Mirror the same rule: only create the global `UserRole` when `invite.role.name == "admin"`:

```python
    is_admin_role = invite.role and invite.role.name == "admin"
    user = User(..., is_admin=is_admin_role, ...)   # unchanged
    db.add(user)
    await db.flush()
    if is_admin_role:
        db.add(UserRole(user_id=user.id, role_id=invite.role_id, scope_type="global"))
    # Non-admin invitees start with no role; project assignment follows.
```

> The invite still records the intended role (`invite.role_id`); for non-admins it no longer becomes a global grant. An admin assigns projects after acceptance. (No schema change required here.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_user_creation_transition.py -v`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/users.py backend/app/api/v1/invites.py backend/tests/test_user_creation_transition.py
git commit -m "feat(rbac): non-admin users/invites no longer get a global role"
```

---

### Task 9: Migration — convert existing global developer/viewer to per-project rows

**Files:**
- Create: `backend/alembic/versions/023_project_scoped_roles.py`
- Test: `backend/tests/test_migration_023_logic.py` (tests the transform as a pure helper run against in-memory SQLite)

**Interfaces:**
- Produces: after upgrade, no global `developer`/`viewer` `UserRole` rows remain; each affected user has a project-scoped row of the same role for every project that existed at migration time. `admin` rows untouched.

- [ ] **Step 1: Write the failing test (transform helper)**

To keep the data transform testable without a live Postgres, put the logic in a module-level function the migration calls, and unit-test it. Create `backend/tests/test_migration_023_logic.py`:

```python
import os, uuid
import pytest, pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_project


async def _role(db, name, perms):
    from app.models.role import Role
    r = Role(id=uuid.uuid4(), name=name, permissions=perms, is_system=True)
    db.add(r); await db.flush(); return r


async def _user(db, email):
    from app.models.user import User
    u = User(id=uuid.uuid4(), email=email, name="U", is_active=True)
    db.add(u); await db.flush(); return u


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_convert_global_nonadmin_to_per_project(sf):
    from app.services.role_migration import convert_global_nonadmin_to_scoped
    from app.models.role import UserRole
    async with sf() as db:
        dev = await _role(db, "developer", ["pipelines.read"])
        admin = await _role(db, "admin", ["admin"])
        u_dev = await _user(db, "dev@e.com")
        u_admin = await _user(db, "admin@e.com")
        p1 = await seed_project(db, "P1"); p2 = await seed_project(db, "P2")
        db.add(UserRole(id=uuid.uuid4(), user_id=u_dev.id, role_id=dev.id, scope_type="global"))
        db.add(UserRole(id=uuid.uuid4(), user_id=u_admin.id, role_id=admin.id, scope_type="global"))
        await db.commit()

        await convert_global_nonadmin_to_scoped(db)
        await db.commit()

        dev_rows = (await db.execute(select(UserRole).where(UserRole.user_id == u_dev.id))).scalars().all()
        admin_rows = (await db.execute(select(UserRole).where(UserRole.user_id == u_admin.id))).scalars().all()

    assert {r.scope_type for r in dev_rows} == {"project"}
    assert {r.scope_id for r in dev_rows} == {p1, p2}
    assert all(r.role_id == dev.id for r in dev_rows)
    assert len(admin_rows) == 1 and admin_rows[0].scope_type == "global"  # untouched
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_migration_023_logic.py -v`
Expected: FAIL — `app.services.role_migration` doesn't exist.

- [ ] **Step 2: Run test to verify it fails**

Run the command above.

- [ ] **Step 3: Implement the transform helper**

Create `backend/app/services/role_migration.py`:

```python
"""Reusable transform for migration 023: convert global developer/viewer role
assignments into per-project assignments across all existing projects."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def convert_global_nonadmin_to_scoped(db: AsyncSession) -> int:
    from app.models.project import Project
    from app.models.role import Role, UserRole

    project_ids = [p for (p,) in (await db.execute(select(Project.id))).all()]
    nonadmin_role_ids = {
        rid for (rid,) in (
            await db.execute(select(Role.id).where(Role.name.in_(("developer", "viewer"))))
        ).all()
    }
    global_rows = (await db.execute(
        select(UserRole).where(
            UserRole.scope_type == "global",
            UserRole.role_id.in_(nonadmin_role_ids),
        )
    )).scalars().all()

    converted = 0
    for ur in global_rows:
        for pid in project_ids:
            db.add(UserRole(id=uuid.uuid4(), user_id=ur.user_id, role_id=ur.role_id,
                            scope_type="project", scope_id=pid))
        await db.delete(ur)
        converted += 1
    return converted
```

- [ ] **Step 4: Write the Alembic migration that calls it**

Create `backend/alembic/versions/023_project_scoped_roles.py` (chains from head `022`). The migration runs synchronously, so it uses a sync connection and raw SQL mirroring the helper logic (the async helper is for the test; the migration body is sync to match Alembic's runtime):

```python
"""Convert global developer/viewer roles to per-project assignments

Revision ID: 023
Revises: 022
Create Date: 2026-06-23
"""
import uuid
from alembic import op
import sqlalchemy as sa

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    project_ids = [r[0] for r in conn.execute(sa.text("SELECT id FROM projects")).fetchall()]
    nonadmin = [r[0] for r in conn.execute(
        sa.text("SELECT id FROM roles WHERE name IN ('developer','viewer')")
    ).fetchall()]
    if not nonadmin:
        return
    rows = conn.execute(sa.text(
        "SELECT id, user_id, role_id FROM user_roles "
        "WHERE scope_type='global' AND role_id = ANY(:rids)"
    ), {"rids": nonadmin}).fetchall()
    for ur_id, user_id, role_id in rows:
        for pid in project_ids:
            conn.execute(sa.text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_type, scope_id) "
                "VALUES (:id, :uid, :rid, 'project', :pid)"
            ), {"id": str(uuid.uuid4()), "uid": user_id, "rid": role_id, "pid": pid})
        conn.execute(sa.text("DELETE FROM user_roles WHERE id=:id"), {"id": ur_id})


def downgrade() -> None:
    # Best-effort: collapse each user's project-scoped developer/viewer rows back
    # to a single global row of that role, then drop the project rows.
    conn = op.get_bind()
    nonadmin = [r[0] for r in conn.execute(
        sa.text("SELECT id FROM roles WHERE name IN ('developer','viewer')")
    ).fetchall()]
    if not nonadmin:
        return
    pairs = conn.execute(sa.text(
        "SELECT DISTINCT user_id, role_id FROM user_roles "
        "WHERE scope_type='project' AND role_id = ANY(:rids)"
    ), {"rids": nonadmin}).fetchall()
    conn.execute(sa.text(
        "DELETE FROM user_roles WHERE scope_type='project' AND role_id = ANY(:rids)"
    ), {"rids": nonadmin})
    for user_id, role_id in pairs:
        conn.execute(sa.text(
            "INSERT INTO user_roles (id, user_id, role_id, scope_type, scope_id) "
            "VALUES (:id, :uid, :rid, 'global', NULL) ON CONFLICT DO NOTHING"
        ), {"id": str(uuid.uuid4()), "uid": user_id, "rid": role_id})
```

> The `ANY(:rids)` form is Postgres-specific (the production DB). The pure-Python helper in `role_migration.py` is what the test exercises; the migration body targets Postgres directly.

- [ ] **Step 5: Run the helper test to verify it passes; verify the migration imports**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_migration_023_logic.py -v`
Expected: PASS (1 passed).
Run: `cd backend && ./.venv/Scripts/python.exe -c "import importlib.util, pathlib; importlib.util.spec_from_file_location('m', pathlib.Path('alembic/versions/023_project_scoped_roles.py'))"` — expect no error (file parses). If `alembic` is installed, `./.venv/Scripts/python.exe -m alembic heads` should show a single head `023`; if alembic isn't in the venv, note that and rely on the chain (`down_revision="022"`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/role_migration.py backend/alembic/versions/023_project_scoped_roles.py backend/tests/test_migration_023_logic.py
git commit -m "feat(rbac): migration converting global developer/viewer to per-project roles"
```

---

### Task 10: Search filtering by accessible projects

**Files:**
- Modify: `backend/app/services/search.py` (build doc `project_id` + filterable; `multi_search` accepts per-index filters)
- Modify: `backend/app/api/v1/search.py` (compute accessible sets, pass filters)
- Test: `backend/tests/test_search_filter.py` (pure filter-string builder)

**Interfaces:**
- Produces: `build_project_filter(accessible) -> str | None` (Meilisearch filter expression, or None for unrestricted).

- [ ] **Step 1: Write the failing test (pure builder)**

Create `backend/tests/test_search_filter.py`:

```python
import os, uuid
os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")


def test_filter_none_for_all():
    from app.services.search import build_project_filter, ALL_PROJECTS
    assert build_project_filter(ALL_PROJECTS) is None


def test_filter_empty_is_match_nothing():
    from app.services.search import build_project_filter
    # Empty set must match NOTHING, not everything.
    assert build_project_filter(set()) == "project_id IN []"


def test_filter_lists_ids():
    from app.services.search import build_project_filter
    a, b = uuid.uuid4(), uuid.uuid4()
    f = build_project_filter({a, b})
    assert f.startswith("project_id IN [")
    assert str(a) in f and str(b) in f
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_search_filter.py -v`
Expected: FAIL.

- [ ] **Step 2: Run test to verify it fails**

Run the command above.

- [ ] **Step 3: Edit `search.py`**

- Re-export the sentinel for convenience: `from app.core.access import ALL_PROJECTS`.
- Add `project_id` to the **projects** doc (`_project_doc`: `"project_id": str(project.id)`) and the **builds** doc (`_build_doc`: needs the build's project — set it from a passed argument; update `index_build` callers and `sync_all` to join `Build→Pipeline` for `project_id`). Add `"project_id"` to `filterable_attributes` for `INDEX_PROJECTS` and `INDEX_BUILDS`.
- Add the pure builder:

```python
def build_project_filter(accessible) -> str | None:
    """Meilisearch filter restricting to accessible projects.
    None  -> unrestricted (ALL_PROJECTS). Empty set -> match nothing."""
    if accessible is ALL_PROJECTS:
        return None
    ids = ", ".join(f'"{pid}"' for pid in accessible)
    return f"project_id IN [{ids}]"
```

- Extend `multi_search` to accept `filters: dict[str, str | None] | None` and attach each index's filter to its `SearchParams(..., filter=filters.get(uid))`.

- [ ] **Step 4: Edit `search.py` API endpoint**

In `app/api/v1/search.py`, switch to `Depends(get_current_active_user)`, compute per-index accessible sets, and pass filters:

```python
from app.core.access import accessible_project_ids
from app.services.search import build_project_filter, INDEX_PROJECTS, INDEX_PIPELINES, INDEX_BUILDS

filters = {
    INDEX_PROJECTS: build_project_filter(accessible_project_ids(user, "projects.read")),
    INDEX_PIPELINES: build_project_filter(accessible_project_ids(user, "pipelines.read")),
    INDEX_BUILDS: build_project_filter(accessible_project_ids(user, "builds.read")),
}
results = await multi_search(q, limit=limit, filters=filters)
```

> A full backfill of the new `project_id` field on existing build/project docs happens via the existing `sync_all` on startup (it re-indexes everything). Note this in the report.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_search_filter.py -v`
Expected: PASS (3 passed).

> The live multi_search against Meilisearch is integration-tested manually (no Meilisearch in unit tests); the pure builder is the unit-tested seam.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/search.py backend/app/api/v1/search.py backend/tests/test_search_filter.py
git commit -m "feat(rbac): filter search results by accessible projects"
```

---

### Task 11: Frontend — project-assignment UI + empty states

**Files:**
- Modify: `frontend/src/lib/api.ts` (add `projectsApi.members`; confirm `usersApi.assignRole`/`removeRole` signatures cover `scope_type`/`scope_id`)
- Create: `frontend/src/components/ProjectAssignmentsEditor.tsx`
- Modify: the user-management component used by `settings/page.tsx` (mount the editor per user)
- Modify: `frontend/src/app/projects/page.tsx`, `pipelines/page.tsx`, `builds/page.tsx` (empty states)

**Interfaces:**
- Consumes: `usersApi.assignRole(userId, {role_id, scope_type, scope_id})`, `usersApi.removeRole(userId, userRoleId)`, `projectsApi.list()`, `rolesApi.list()`.

- [ ] **Step 1: Add the members API client function**

In `frontend/src/lib/api.ts`, in `projectsApi`, add:

```ts
members: (projectId: string) =>
  fetchApi<{ user_id: string; email: string; name: string; role_name: string }[]>(
    `/api/v1/projects/${projectId}/members`,
  ),
```

Confirm `usersApi.assignRole` passes `scope_type` and `scope_id` through (the existing `AssignRoleRequest` type already includes them per `src/lib/api.ts`). If `scope_id` isn't in the type, add `scope_id?: string | null` and `scope_type?: string`.

- [ ] **Step 2: Build the assignments editor component**

Create `frontend/src/components/ProjectAssignmentsEditor.tsx`. It renders a user's current `{project, role}` assignments (from `user.roles` filtered to `scope_type === "project"`) as removable chips, plus an "Assign project" row (project `<select>` + role `<select>` of developer/viewer) that calls `usersApi.assignRole`. On add/remove it calls the parent `onChanged()` to refresh the user. Representative implementation:

```tsx
"use client";
import { useEffect, useState } from "react";
import { usersApi, projectsApi, rolesApi, type UserDetail, type Role, type Project } from "@/lib/api";

export function ProjectAssignmentsEditor({ user, onChanged }: { user: UserDetail; onChanged: () => void }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [projectId, setProjectId] = useState("");
  const [roleId, setRoleId] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    projectsApi.list().then(setProjects).catch(() => {});
    rolesApi.list().then((rs) => setRoles(rs.filter((r) => r.name !== "admin"))).catch(() => {});
  }, []);

  const projectRoles = user.roles.filter((r) => r.scope_type === "project");

  async function add() {
    if (!projectId || !roleId) return;
    setBusy(true);
    try {
      await usersApi.assignRole(user.id, { role_id: roleId, scope_type: "project", scope_id: projectId });
      setProjectId(""); setRoleId(""); onChanged();
    } finally { setBusy(false); }
  }

  async function remove(userRoleId: string) {
    setBusy(true);
    try { await usersApi.removeRole(user.id, userRoleId); onChanged(); }
    finally { setBusy(false); }
  }

  if (user.is_admin) return <p className="text-sm text-muted-foreground">Admin — access to all projects.</p>;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        {projectRoles.length === 0 && <span className="text-sm text-muted-foreground">No projects assigned.</span>}
        {projectRoles.map((r) => (
          <span key={r.id} className="inline-flex items-center gap-1 rounded bg-muted px-2 py-1 text-xs">
            {r.project_name ?? r.scope_id} · {r.role_name}
            <button disabled={busy} onClick={() => remove(r.id)} aria-label="Remove" className="ml-1">×</button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <select value={projectId} onChange={(e) => setProjectId(e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value="">Select project…</option>
          {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </select>
        <select value={roleId} onChange={(e) => setRoleId(e.target.value)} className="border rounded px-2 py-1 text-sm">
          <option value="">Role…</option>
          {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
        </select>
        <button disabled={busy || !projectId || !roleId} onClick={add} className="border rounded px-3 py-1 text-sm">Assign</button>
      </div>
    </div>
  );
}
```

> Match the project's actual UI primitives (Badge/Button/Select components) by following the patterns already used in the user-management component rather than the raw HTML above. The logic (filter `scope_type === "project"`, assign/remove, refresh) is what matters.

- [ ] **Step 3: Mount it in the user-management component**

In the component that renders the users table for `settings/page.tsx` (the one receiving `isAdmin`), render `<ProjectAssignmentsEditor user={u} onChanged={reloadUsers} />` in each user's expanded/detail row. Ensure the user objects come from `usersApi.get(id)` / `usersApi.list()` so they include `roles` with `scope_type`, `scope_id`, `project_name`. Update create-user UI to choose **Admin** vs **Member**; for Member, after creation, surface the assignments editor.

- [ ] **Step 4: Add empty states**

In `projects/page.tsx`, `pipelines/page.tsx`, `builds/page.tsx`, when the fetched list is empty AND the current user is not an admin, render: "No projects assigned yet — ask an admin to grant you access." (Use the existing empty-state pattern on each page.)

- [ ] **Step 5: Verify the build**

Run: `cd frontend && npx tsc --noEmit`
Expected: no type errors. (The repo's `npm run lint` is known-broken under Next 16 — do not rely on it.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/ProjectAssignmentsEditor.tsx frontend/src/app/settings/page.tsx frontend/src/app/projects/page.tsx frontend/src/app/pipelines/page.tsx frontend/src/app/builds/page.tsx
git commit -m "feat(rbac): project-assignment UI + non-admin empty states"
```

---

### Task 12: Full regression sweep

**Files:** none (verification only)

- [ ] **Step 1: Backend suite**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior tests + the new `test_access_core`, `test_project_scoped_*`, `test_assignment_api`, `test_user_creation_transition`, `test_migration_023_logic`, `test_search_filter`).

- [ ] **Step 2: Frontend typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Manual smoke (optional, live stack)**

As admin: create a Member user, assign them developer on Project A and viewer on Project B. Log in as that user: confirm only A and B appear in projects, only their pipelines/builds list, builds in A can be triggered but B's cannot (403), search returns only A/B entities, and an unassigned project's pipeline returns 403 on direct URL. Confirm an admin still sees everything.

---

## Notes for the implementer

- **Why list endpoints return `[]` not 403:** a non-admin with zero (or partial) assignments is a normal state; surfacing it as an empty list is friendlier and avoids leaking which projects exist. Detail endpoints still 403 (existing `check_scoped_permission` behavior).
- **Enforcement is safe to land before the migration:** existing users still hold global developer/viewer roles, so `accessible_project_ids` returns `ALL_PROJECTS` for them until Task 9 runs — Tasks 2–8 cause no visibility change for current users. Task 9 (migration) is the switch that flips them to scoped.
- **PAT scope composes for free:** `accessible_project_ids` calls `effective_permissions`/`effective_scoped_permissions`, which already intersect with the active token's scopes.
- **Do not change `registry.py`** or introduce hierarchy inheritance — both are explicit non-goals.
- **Agent-token artifact upload stays untouched** — only user-facing artifact endpoints get scoped.
