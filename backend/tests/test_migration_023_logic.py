import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_role, seed_user, seed_project


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_convert_global_nonadmin_to_per_project(sf):
    from app.services.role_migration import convert_global_nonadmin_to_scoped
    from app.models.role import UserRole

    async with sf() as db:
        dev_id = await seed_role(db, "developer", ["pipelines.read"])
        admin_id = await seed_role(db, "admin", ["admin"])
        u_dev_id = await seed_user(db)
        u_admin_id = await seed_user(db)
        p1 = await seed_project(db, "P1")
        p2 = await seed_project(db, "P2")

        db.add(UserRole(id=uuid.uuid4(), user_id=u_dev_id, role_id=dev_id, scope_type="global"))
        db.add(UserRole(id=uuid.uuid4(), user_id=u_admin_id, role_id=admin_id, scope_type="global"))
        await db.commit()

        await convert_global_nonadmin_to_scoped(db)
        await db.commit()

        dev_rows = (
            await db.execute(select(UserRole).where(UserRole.user_id == u_dev_id))
        ).scalars().all()
        admin_rows = (
            await db.execute(select(UserRole).where(UserRole.user_id == u_admin_id))
        ).scalars().all()

    assert {r.scope_type for r in dev_rows} == {"project"}
    assert {r.scope_id for r in dev_rows} == {p1, p2}
    assert all(r.role_id == dev_id for r in dev_rows)
    assert len(admin_rows) == 1 and admin_rows[0].scope_type == "global"  # untouched
