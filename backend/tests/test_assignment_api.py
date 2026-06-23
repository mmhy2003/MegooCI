import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_user, seed_role, seed_project


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_assign_project_role_rejects_missing_project(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    async with sf() as db:
        uid = await seed_user(db)
        dev = await seed_role(db, "developer", ["pipelines.read"])
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
        uid = await seed_user(db)
        admin = await seed_role(db, "admin", ["admin"])
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
        uid = await seed_user(db)
        dev = await seed_role(db, "developer", ["pipelines.manage"])
        view = await seed_role(db, "viewer", ["pipelines.read"])
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
