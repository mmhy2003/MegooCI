import os, sys, types, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_role, seed_user


@pytest.fixture(autouse=True)
def _patch_search(monkeypatch):
    """Stub app.services.search so tests don't need meilisearch installed."""
    async def _noop(*args, **kwargs):
        return None

    stub = types.ModuleType("app.services.search")
    stub.index_project = _noop
    stub.remove_project = _noop
    monkeypatch.setitem(sys.modules, "app.services.search", stub)

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
        res = await list_projects(skip=0, limit=20, db=db, _current_user=user)
    ids = {p.id for p in res.items}
    assert ids == {a} and b not in ids
    assert res.total == 1


async def test_list_projects_admin_sees_all(sf):
    from app.api.v1.projects import list_projects
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    admin = make_user(is_admin=True)
    async with sf() as db:
        res = await list_projects(skip=0, limit=20, db=db, _current_user=admin)
    assert {p.id for p in res.items} == {a, b}
    assert res.total == 2


async def test_list_projects_zero_assignments_empty(sf):
    from app.api.v1.projects import list_projects
    async with sf() as db:
        await seed_project(db, "A"); await db.commit()
    user = make_user()
    async with sf() as db:
        res = await list_projects(skip=0, limit=20, db=db, _current_user=user)
    assert res.items == [] and res.total == 0


async def test_list_projects_total_counts_beyond_page(sf):
    """`total` reflects all accessible projects, not just the returned page."""
    from app.api.v1.projects import list_projects
    async with sf() as db:
        for i in range(3):
            await seed_project(db, f"P{i}")
        await db.commit()
    admin = make_user(is_admin=True)
    async with sf() as db:
        res = await list_projects(skip=0, limit=2, db=db, _current_user=admin)
    assert len(res.items) == 2
    assert res.total == 3


async def test_list_projects_skip_pages_through(sf):
    """skip/limit paginate without overlap and total stays constant."""
    from app.api.v1.projects import list_projects
    async with sf() as db:
        ids = {await seed_project(db, f"P{i}") for i in range(3)}
        await db.commit()
    admin = make_user(is_admin=True)
    async with sf() as db:
        page1 = await list_projects(skip=0, limit=2, db=db, _current_user=admin)
        page2 = await list_projects(skip=2, limit=2, db=db, _current_user=admin)
    got = {p.id for p in page1.items} | {p.id for p in page2.items}
    assert got == ids
    assert len(page2.items) == 1
    assert page1.total == page2.total == 3


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
    from sqlalchemy import select, text
    async with sf() as db:
        a = await seed_project(db, "A"); await db.commit()
    user = make_user(is_admin=True)
    # Seed real user + role rows so the UserRole FK constraints are satisfied.
    async with sf() as db:
        real_user_id = await seed_user(db)
        real_role_id = await seed_role(db, name="viewer", permissions=list(VIEW))
        await db.execute(
            text(
                "INSERT INTO user_roles (id, user_id, role_id, scope_type, scope_id)"
                " VALUES (:id, :uid, :rid, 'project', :sid)"
            ),
            {"id": uuid.uuid4().hex, "uid": real_user_id.hex,
             "rid": real_role_id.hex, "sid": a.hex},
        )
        await db.commit()
    async with sf() as db:
        await delete_project(a, force=False, db=db, current_user=user)
    async with sf() as db:
        rows = (await db.execute(
            select(UserRole).where(UserRole.scope_type == "project", UserRole.scope_id == a)
        )).scalars().all()
    assert rows == []
