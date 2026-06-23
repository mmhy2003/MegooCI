import os
import pytest, pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_role


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
        dev = await seed_role(db, "developer", ["pipelines.read"])
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
        admin = await seed_role(db, "admin", ["admin"])
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
