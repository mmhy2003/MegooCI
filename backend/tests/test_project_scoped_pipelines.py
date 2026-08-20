import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import build_inmemory_factory, make_role, make_user, seed_project, seed_pipeline

DEV = ["projects.read", "pipelines.read", "pipelines.manage", "builds.read"]
VIEW = ["projects.read", "pipelines.read", "builds.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_list_pipelines_filtered(sf):
    from app.api.v1.pipelines import list_pipelines
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pa = await seed_pipeline(db, a); pb = await seed_pipeline(db, b)
        await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        res = await list_pipelines(project_id=None, skip=0, limit=20, db=db, _current_user=user)
    assert {p.id for p in res.items} == {pa}
    assert res.total == 1


async def test_list_pipelines_project_filter_inaccessible_empty(sf):
    from app.api.v1.pipelines import list_pipelines
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        await seed_pipeline(db, b); await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        res = await list_pipelines(project_id=b, skip=0, limit=20, db=db, _current_user=user)
    assert res.items == [] and res.total == 0


async def test_list_pipelines_total_counts_beyond_page(sf):
    """`total` reflects all matching pipelines, not just the returned page."""
    from app.api.v1.pipelines import list_pipelines
    async with sf() as db:
        a = await seed_project(db, "A")
        for _ in range(3):
            await seed_pipeline(db, a)
        await db.commit()
    admin = make_user(is_admin=True)
    async with sf() as db:
        res = await list_pipelines(project_id=None, skip=0, limit=2, db=db, _current_user=admin)
    assert len(res.items) == 2
    assert res.total == 3


async def test_list_pipelines_total_respects_project_filter(sf):
    """With a project_id filter, total counts only that project's pipelines."""
    from app.api.v1.pipelines import list_pipelines
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pa = await seed_pipeline(db, a)
        await seed_pipeline(db, b); await seed_pipeline(db, b)
        await db.commit()
    admin = make_user(is_admin=True)
    async with sf() as db:
        res = await list_pipelines(project_id=a, skip=0, limit=20, db=db, _current_user=admin)
    assert {p.id for p in res.items} == {pa}
    assert res.total == 1


async def test_get_pipeline_scoped_403(sf):
    from app.api.v1.pipelines import get_pipeline
    async with sf() as db:
        a = await seed_project(db, "A"); b = await seed_project(db, "B")
        pb = await seed_pipeline(db, b); await db.commit()
    user = make_user(project_roles=[(a, make_role("viewer", VIEW))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await get_pipeline(pb, db=db, _current_user=user)
    assert exc.value.status_code == 403
