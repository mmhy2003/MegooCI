"""A webhook for a pipeline that already has a queued run coalesces into it."""

import types
import uuid

import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from tests._concurrency import create_concurrency_indexes


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Pipeline, Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


GOOD_YAML = "name: p\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"


async def test_webhook_coalesces_repeated_pushes(session_factory):
    from app.api.v1.webhooks_git import _enqueue_matching_builds
    from app.models.build import Build
    from app.models.pipeline import Pipeline

    project_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(Pipeline(id=uuid.uuid4(), project_id=project_id, name="p",
                        default_branch="main", yaml_content=GOOD_YAML, enabled=True,
                        created_by=uuid.uuid4(),
                        source_repo_url="https://example.com/r.git"))
        await db.commit()

        repo = types.SimpleNamespace(project_id=project_id, id=uuid.uuid4(),
                                     repo_url="https://example.com/r.git")
        event1 = types.SimpleNamespace(branch="main", commit_sha="aaa")
        event2 = types.SimpleNamespace(branch="main", commit_sha="bbb")

        ids1 = await _enqueue_matching_builds(db, repo, event1)
        ids2 = await _enqueue_matching_builds(db, repo, event2)

    assert len(ids1) == 1            # first push created a build
    assert ids2 == []                # second push coalesced — nothing new to enqueue
    async with session_factory() as db:
        pending = (await db.execute(select(Build).where(Build.status == "pending"))).scalars().all()
        assert len(pending) == 1
        assert pending[0].commit_sha == "bbb"   # latest wins
