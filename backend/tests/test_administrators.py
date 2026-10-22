"""Administrator is one setting: the is_admin flag and the global admin role
always change together, with guards against locking everyone out."""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_project, seed_role


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _user(db, *, flag=False, admin_role=None, active=True) -> uuid.UUID:
    from app.models.role import UserRole
    from app.models.user import User
    user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="U",
                is_admin=flag, is_active=active)
    db.add(user)
    await db.flush()
    if admin_role is not None:
        db.add(UserRole(user_id=user.id, role_id=admin_role, scope_type="global"))
        await db.flush()
    return user.id


async def _state(sf, user_id):
    """(is_admin, number of global admin rows, all of the user's rows)."""
    from app.models.role import Role, UserRole
    from app.models.user import User
    async with sf() as db:
        user = await db.get(User, user_id)
        rows = (await db.execute(select(UserRole).where(UserRole.user_id == user_id))).scalars().all()
        admin_ids = set((await db.execute(select(Role.id).where(Role.name == "admin"))).scalars())
    admin_rows = [r for r in rows if r.scope_type == "global" and r.role_id in admin_ids]
    return user.is_admin, len(admin_rows), rows


async def _set(sf, user_id, on, actor_id=None):
    from app.models.user import User
    from app.services.administrators import set_administrator
    async with sf() as db:
        user = await db.get(User, user_id)
        await set_administrator(db, user, on, actor_id=actor_id)
        await db.commit()


async def test_turning_on_sets_flag_and_adds_global_admin_role(sf):
    from app.models.audit import AuditLogEntry
    async with sf() as db:
        await seed_role(db, "admin", ["admin"])
        uid = await _user(db)
        await db.commit()
    await _set(sf, uid, True)
    flag, admin_rows, _ = await _state(sf, uid)
    assert flag is True and admin_rows == 1
    async with sf() as db:
        actions = (await db.execute(
            select(AuditLogEntry.action).where(AuditLogEntry.target_id == uid))).scalars().all()
    assert actions == ["administrator_granted"]


async def test_turning_on_a_role_only_user_adds_only_the_flag(sf):
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        uid = await _user(db, admin_role=admin)
        await db.commit()
    await _set(sf, uid, True)
    flag, admin_rows, _ = await _state(sf, uid)
    assert flag is True and admin_rows == 1


async def test_turning_off_a_flag_only_user_clears_the_flag(sf):
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        await _user(db, flag=True, admin_role=admin)
        uid = await _user(db, flag=True)
        await db.commit()
    await _set(sf, uid, False)
    flag, admin_rows, _ = await _state(sf, uid)
    assert flag is False and admin_rows == 0


async def test_turning_off_removes_admin_halves_but_keeps_project_rows(sf):
    from app.models.role import UserRole
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        viewer = await seed_role(db, "viewer", ["projects.read"])
        pid = await seed_project(db, "A")
        await _user(db, flag=True, admin_role=admin)
        uid = await _user(db, flag=True, admin_role=admin)
        db.add(UserRole(user_id=uid, role_id=viewer, scope_type="project", scope_id=pid))
        await db.commit()
    await _set(sf, uid, False)
    flag, admin_rows, rows = await _state(sf, uid)
    assert flag is False and admin_rows == 0
    assert [(r.scope_type, r.scope_id, r.role_id) for r in rows] == [("project", pid, viewer)]


async def test_cannot_turn_off_yourself(sf):
    from app.services.administrators import AdministratorChangeError
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        await _user(db, flag=True, admin_role=admin)
        uid = await _user(db, flag=True, admin_role=admin)
        await db.commit()
    with pytest.raises(AdministratorChangeError) as exc:
        await _set(sf, uid, False, actor_id=uid)
    assert str(exc.value) == "You cannot remove your own administrator access"
    assert (await _state(sf, uid))[:2] == (True, 1)


async def test_cannot_turn_off_the_last_active_administrator(sf):
    from app.services.administrators import AdministratorChangeError
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        await _user(db, flag=True, admin_role=admin, active=False)
        uid = await _user(db, flag=True, admin_role=admin)
        await db.commit()
    with pytest.raises(AdministratorChangeError) as exc:
        await _set(sf, uid, False)
    assert str(exc.value) == "There must be at least one active administrator"
    assert (await _state(sf, uid))[:2] == (True, 1)


async def test_a_half_set_up_user_counts_as_another_administrator(sf):
    async with sf() as db:
        admin = await seed_role(db, "admin", ["admin"])
        await _user(db, admin_role=admin)
        uid = await _user(db, flag=True, admin_role=admin)
        await db.commit()
    await _set(sf, uid, False)
    assert (await _state(sf, uid))[:2] == (False, 0)


async def test_turning_on_without_an_admin_role_sets_only_the_flag(sf):
    async with sf() as db:
        uid = await _user(db)
        await db.commit()
    await _set(sf, uid, True)
    flag, admin_rows, rows = await _state(sf, uid)
    assert flag is True and rows == []
