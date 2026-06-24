import os
import uuid

import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._rbac import build_inmemory_factory, seed_project, seed_user, seed_role


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_project_members_includes_user_role_id(sf):
    from app.api.v1.projects import list_project_members
    from app.models.role import UserRole

    async with sf() as db:
        pid = await seed_project(db, "P")
        uid = await seed_user(db)
        rid = await seed_role(db, "developer", ["pipelines.read"])
        ur_id = uuid.uuid4()
        db.add(UserRole(id=ur_id, user_id=uid, role_id=rid,
                        scope_type="project", scope_id=pid))
        await db.commit()

    async with sf() as db:
        members = await list_project_members(pid, db=db, _current_user=None)

    assert len(members) == 1
    m = members[0]
    assert m["user_role_id"] == ur_id
    assert m["user_id"] == uid
    assert m["role_name"] == "developer"
    assert m["email"] and m["name"]


async def test_list_project_members_only_this_project(sf):
    from app.api.v1.projects import list_project_members
    from app.models.role import UserRole

    async with sf() as db:
        p1 = await seed_project(db, "P1")
        p2 = await seed_project(db, "P2")
        uid = await seed_user(db)
        rid = await seed_role(db, "viewer", ["pipelines.read"])
        db.add(UserRole(id=uuid.uuid4(), user_id=uid, role_id=rid,
                        scope_type="project", scope_id=p2))
        await db.commit()

    async with sf() as db:
        members = await list_project_members(p1, db=db, _current_user=None)

    assert members == []
