"""The first push to a new repository starts several uploads at once, and each
of them creates the repository if it is not there yet. Losing that race must
not fail the request."""
import os
import uuid

import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.api.v1.registry_oci import _get_or_create_repo
from app.models.project import Project
from app.models.registry import ContainerRepository
from tests._rbac import build_inmemory_factory, seed_project


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _repo_ids(sf, project_id) -> list[uuid.UUID]:
    async with sf() as db:
        result = await db.execute(
            sa.select(ContainerRepository.id).where(
                ContainerRepository.project_id == project_id
            )
        )
        return list(result.scalars())


def _another_request_creates_it_first(db, monkeypatch, project_id, name) -> uuid.UUID:
    """Have the repository appear right after *db* has looked for it and found
    nothing, as when a second upload of the same push gets there first."""
    winner = uuid.uuid4()
    real_execute = db.execute
    calls = 0

    async def execute(statement, *args, **kwargs):
        nonlocal calls
        result = await real_execute(statement, *args, **kwargs)
        calls += 1
        if calls == 1:
            await real_execute(
                sa.insert(ContainerRepository).values(
                    id=winner, project_id=project_id, name=name
                )
            )
        return result

    monkeypatch.setattr(db, "execute", execute)
    return winner


async def test_creates_the_repository_on_first_use(sf):
    async with sf() as db:
        project = await db.get(Project, await seed_project(db))
        repo = await _get_or_create_repo(db, project, "frontend")
        await db.commit()

    assert repo.name == "frontend"
    assert await _repo_ids(sf, project.id) == [repo.id]


async def test_returns_the_repository_that_is_already_there(sf):
    async with sf() as db:
        project = await db.get(Project, await seed_project(db))
        first = await _get_or_create_repo(db, project, "frontend")
        await db.commit()
        second = await _get_or_create_repo(db, project, "frontend")
        await db.commit()

    assert second.id == first.id
    assert await _repo_ids(sf, project.id) == [first.id]


async def test_losing_the_race_returns_the_other_requests_repository(sf, monkeypatch):
    async with sf() as db:
        project = await db.get(Project, await seed_project(db))
        winner = _another_request_creates_it_first(db, monkeypatch, project.id, "frontend")

        repo = await _get_or_create_repo(db, project, "frontend")

        assert repo.id == winner
        assert repo.name == "frontend"
        await db.commit()

    assert await _repo_ids(sf, project.id) == [winner]


async def test_losing_the_race_keeps_the_rest_of_the_request(sf, monkeypatch):
    async with sf() as db:
        project = await db.get(Project, await seed_project(db))
        project.description = "changed earlier in the same request"
        _another_request_creates_it_first(db, monkeypatch, project.id, "frontend")

        await _get_or_create_repo(db, project, "frontend")
        await db.commit()

    async with sf() as db:
        stored = await db.get(Project, project.id)
        assert stored.description == "changed earlier in the same request"
