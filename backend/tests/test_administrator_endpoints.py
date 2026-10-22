"""Every endpoint that changes admin changes the flag and the role together."""
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_project, seed_role


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _user(db, *, flag=False, admin_role=None) -> uuid.UUID:
    from app.models.role import UserRole
    from app.models.user import User
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="U",
                is_admin=flag, is_active=True)
    db.add(user)
    await db.flush()
    if admin_role is not None:
        db.add(UserRole(user_id=user.id, role_id=admin_role, scope_type="global"))
        await db.flush()
    return user.id


async def _world(sf):
    """An acting full admin, a second full admin, a plain user and the roles."""
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        viewer = await seed_role(db, "viewer", ["projects.read"])
        actor = await _user(db, flag=True, admin_role=admin)
        other = await _user(db, flag=True, admin_role=admin)
        plain = await _user(db)
        await db.commit()
    return {"admin": admin, "viewer": viewer, "actor": actor, "other": other, "plain": plain}


async def _admin_state(sf, user_id):
    from app.models.role import UserRole
    from app.models.user import User
    async with sf() as db:
        user = await db.get(User, user_id)
        rows = (await db.execute(select(UserRole).where(
            UserRole.user_id == user_id, UserRole.scope_type == "global"))).scalars().all()
    return user.is_admin, len(rows)


async def _call(sf, fn, *args, actor, **kwargs):
    from app.models.user import User
    async with sf() as db:
        current = await db.get(User, actor)
        result = await fn(*args, db=db, current_user=current, **kwargs)
        await db.commit()
    return result


async def test_put_is_admin_true_adds_the_role(sf):
    from app.api.v1.users import update_user
    from app.schemas.users import UserUpdateRequest
    w = await _world(sf)
    await _call(sf, update_user, w["plain"], UserUpdateRequest(is_admin=True), actor=w["actor"])
    assert await _admin_state(sf, w["plain"]) == (True, 1)


async def test_put_is_admin_false_removes_the_role_too(sf):
    from app.api.v1.users import update_user
    from app.schemas.users import UserUpdateRequest
    w = await _world(sf)
    await _call(sf, update_user, w["other"], UserUpdateRequest(is_admin=False), actor=w["actor"])
    assert await _admin_state(sf, w["other"]) == (False, 0)


async def test_assigning_the_admin_role_globally_sets_the_flag(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    w = await _world(sf)
    await _call(sf, assign_role, w["plain"], UserRoleAssign(role_id=w["admin"]), actor=w["actor"])
    assert await _admin_state(sf, w["plain"]) == (True, 1)


async def test_removing_the_global_admin_row_clears_the_flag(sf):
    from app.api.v1.users import remove_role
    from app.models.role import UserRole
    w = await _world(sf)
    async with sf() as db:
        row = (await db.execute(select(UserRole.id).where(UserRole.user_id == w["other"]))).scalar_one()
    await _call(sf, remove_role, w["other"], row, actor=w["actor"])
    assert await _admin_state(sf, w["other"]) == (False, 0)


async def test_removing_your_own_global_admin_row_is_refused(sf):
    from app.api.v1.users import remove_role
    from app.models.role import UserRole
    w = await _world(sf)
    async with sf() as db:
        row = (await db.execute(select(UserRole.id).where(UserRole.user_id == w["actor"]))).scalar_one()
    with pytest.raises(HTTPException) as exc:
        await _call(sf, remove_role, w["actor"], row, actor=w["actor"])
    assert exc.value.status_code == 400
    assert exc.value.detail == "You cannot remove your own administrator access"
    assert await _admin_state(sf, w["actor"]) == (True, 1)


async def test_put_is_admin_false_on_the_last_administrator_is_refused(sf):
    from app.api.v1.users import update_user
    from app.models.user import User
    from app.schemas.users import UserUpdateRequest
    w = await _world(sf)
    async with sf() as db:
        (await db.get(User, w["actor"])).is_active = False
        await db.commit()
    with pytest.raises(HTTPException) as exc:
        await _call(sf, update_user, w["other"], UserUpdateRequest(is_admin=False), actor=w["plain"])
    assert exc.value.status_code == 400
    assert exc.value.detail == "There must be at least one active administrator"


async def test_removing_a_project_row_does_not_touch_admin(sf):
    from app.api.v1.users import remove_role
    from app.models.role import UserRole
    w = await _world(sf)
    async with sf() as db:
        pid = await seed_project(db, "A")
        row = UserRole(user_id=w["plain"], role_id=w["viewer"], scope_type="project", scope_id=pid)
        db.add(row)
        await db.commit()
        row_id = row.id
    await _call(sf, remove_role, w["plain"], row_id, actor=w["actor"])
    assert await _admin_state(sf, w["plain"]) == (False, 0)


async def test_assigning_the_admin_role_to_a_role_only_user_completes_it(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    w = await _world(sf)
    async with sf() as db:
        half = await _user(db, admin_role=w["admin"])
        await db.commit()
    await _call(sf, assign_role, half, UserRoleAssign(role_id=w["admin"]), actor=w["actor"])
    assert await _admin_state(sf, half) == (True, 1)


async def test_assigning_the_admin_role_to_a_full_admin_is_a_conflict(sf):
    from app.api.v1.users import assign_role
    from app.schemas.roles import UserRoleAssign
    w = await _world(sf)
    with pytest.raises(HTTPException) as exc:
        await _call(sf, assign_role, w["other"], UserRoleAssign(role_id=w["admin"]), actor=w["actor"])
    assert exc.value.status_code == 409
