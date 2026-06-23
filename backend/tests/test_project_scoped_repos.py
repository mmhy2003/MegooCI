import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project

VIEW = ["projects.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_repositories_scoped_403(sf):
    from app.api.v1.project_repositories import list_repositories
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await list_repositories(project_id=b, db=db, _current_user=user)
    assert exc.value.status_code == 403


async def test_list_repositories_member_ok(sf):
    from app.api.v1.project_repositories import list_repositories
    async with sf() as db:
        a = await seed_project(db, "A"); await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        rows = await list_repositories(project_id=a, db=db, _current_user=user)
    assert rows == []
