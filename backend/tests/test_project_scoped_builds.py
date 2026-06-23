import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_pipeline, seed_build

DEV = ["pipelines.read", "builds.read", "builds.manage"]
VIEW = ["pipelines.read", "builds.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_builds_filtered_by_project(sf):
    from app.api.v1.builds import list_builds
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pa = await seed_pipeline(db, a); pb = await seed_pipeline(db, b)
        ba = await seed_build(db, pa); bb = await seed_build(db, pb)
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        rows = await list_builds(pipeline_id=None, skip=0, limit=20, db=db, _current_user=user)
    assert {x.id for x in rows} == {ba}


async def test_get_build_inaccessible_403(sf):
    from app.api.v1.builds import get_build
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pb = await seed_pipeline(db, b); bb = await seed_build(db, pb)
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await get_build(bb, db=db, _current_user=user)
    assert exc.value.status_code == 403
