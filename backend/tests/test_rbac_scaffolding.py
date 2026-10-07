"""The in-memory test database must round-trip PostgreSQL ARRAY columns."""
import os
import uuid

import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._mcp import seed_member, seed_token
from tests._rbac import build_inmemory_factory, seed_role


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_role_permissions_round_trip_through_the_orm(sf):
    from app.models.role import Role

    async with sf() as db:
        db.add(Role(id=uuid.uuid4(), name="dev", permissions=["projects.read", "builds.manage"]))
        await db.commit()
    async with sf() as db:
        role = (await db.execute(select(Role))).scalar_one()
    assert role.permissions == ["projects.read", "builds.manage"]


async def test_raw_sql_seed_role_reads_back_as_a_list(sf):
    """seed_role() inserts a JSON string; it must load as a list, not characters."""
    from app.models.role import Role

    async with sf() as db:
        role_id = await seed_role(db, "viewer", ["projects.read"])
        await db.commit()
    async with sf() as db:
        role = await db.get(Role, role_id)
    assert role.permissions == ["projects.read"]


async def test_seeded_member_loads_with_its_permissions(sf):
    from sqlalchemy.orm import selectinload

    from app.core.deps import effective_permissions
    from app.models.role import UserRole
    from app.models.user import User

    async with sf() as db:
        uid = await seed_member(db, ["projects.read", "pipelines.read"])
        await db.commit()
    async with sf() as db:
        user = (await db.execute(
            select(User)
            .options(selectinload(User.user_roles).selectinload(UserRole.role))
            .where(User.id == uid)
        )).scalar_one()
    assert effective_permissions(user) == {"projects.read", "pipelines.read"}


async def test_token_scopes_round_trip(sf):
    from app.core.security import hash_pat
    from app.models.api_token import ApiToken

    async with sf() as db:
        uid = await seed_member(db, ["projects.read"])
        scoped = await seed_token(db, uid, scopes=["read.only"])
        full = await seed_token(db, uid)
        await db.commit()
    async with sf() as db:
        rows = {t.token_hash: t.scopes for t in (await db.execute(select(ApiToken))).scalars()}
    assert rows[hash_pat(scoped)] == ["read.only"]
    assert rows[hash_pat(full)] is None
    assert scoped.startswith("megci_pat_")


async def test_factory_can_be_built_twice_in_one_process(sf):
    engine, _ = await build_inmemory_factory()
    await engine.dispose()
