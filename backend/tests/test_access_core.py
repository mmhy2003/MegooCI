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
