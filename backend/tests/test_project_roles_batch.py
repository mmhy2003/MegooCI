"""Several project assignments in one request: all written, or none."""
import os
import uuid

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, seed_project, seed_role, seed_user


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _world(sf):
    async with sf() as db:
        w = {
            "user": await seed_user(db),
            "viewer": await seed_role(db, "viewer", ["projects.read"]),
            "developer": await seed_role(db, "developer", ["projects.manage"]),
            "admin": await seed_role(db, "admin", ["admin"]),
            "a": await seed_project(db, "A"),
            "b": await seed_project(db, "B"),
        }
        await db.commit()
    return w


async def _batch(sf, user_id, pairs):
    from app.api.v1.users import assign_project_roles
    from app.schemas.roles import ProjectRoleAssignment, ProjectRolesBatch
    body = ProjectRolesBatch(assignments=[
        ProjectRoleAssignment(project_id=p, role_id=r) for p, r in pairs])
    async with sf() as db:
        result = await assign_project_roles(user_id, body, db=db, current_user=None)
        await db.commit()
    return result


async def _rows(sf, user_id):
    from app.models.role import UserRole
    async with sf() as db:
        rows = (await db.execute(select(UserRole).where(UserRole.user_id == user_id))).scalars().all()
    return {(r.scope_id, r.role_id) for r in rows}


async def test_batch_adds_several_projects(sf):
    w = await _world(sf)
    result = await _batch(sf, w["user"], [(w["a"], w["viewer"]), (w["b"], w["developer"])])
    assert await _rows(sf, w["user"]) == {(w["a"], w["viewer"]), (w["b"], w["developer"])}
    assert {r["scope_id"] for r in result} == {w["a"], w["b"]}


async def test_batch_replaces_an_existing_role_on_a_project(sf):
    w = await _world(sf)
    await _batch(sf, w["user"], [(w["a"], w["viewer"])])
    await _batch(sf, w["user"], [(w["a"], w["developer"])])
    assert await _rows(sf, w["user"]) == {(w["a"], w["developer"])}


async def test_batch_with_an_unknown_project_writes_nothing(sf):
    w = await _world(sf)
    with pytest.raises(HTTPException) as exc:
        await _batch(sf, w["user"], [(w["a"], w["viewer"]), (uuid.uuid4(), w["viewer"])])
    assert exc.value.status_code == 400
    assert await _rows(sf, w["user"]) == set()


async def test_batch_with_a_duplicate_project_is_refused(sf):
    w = await _world(sf)
    with pytest.raises(HTTPException) as exc:
        await _batch(sf, w["user"], [(w["a"], w["viewer"]), (w["a"], w["developer"])])
    assert exc.value.status_code == 400
    assert exc.value.detail == "Each project may appear only once"
    assert await _rows(sf, w["user"]) == set()


async def test_batch_refuses_the_admin_role(sf):
    w = await _world(sf)
    with pytest.raises(HTTPException) as exc:
        await _batch(sf, w["user"], [(w["a"], w["admin"])])
    assert exc.value.status_code == 400
    assert exc.value.detail == "The admin role cannot be scoped to a project"
