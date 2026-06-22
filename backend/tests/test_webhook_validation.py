"""A git-webhook build for a pipeline with invalid YAML is marked failed with
a visible validation stage (not a silent stageless build)."""

import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    # MUST match the declared type in tests/test_build_retry.py. @compiles is
    # process-global and last-write-wins across collected test modules, so a
    # divergent mapping here breaks that file's artifact_paths round-trip when
    # the suites run together.
    return "TEXTARRAY"


@pytest_asyncio.fixture
async def session_factory():
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for model in (Pipeline, Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


BAD_YAML = "name: demo\nstages:\n  - steps:\n      - run: echo hi\n"  # missing name


async def test_webhook_invalid_yaml_creates_failed_build(session_factory):
    import types

    from app.api.v1.webhooks_git import _enqueue_matching_builds
    from app.models.build import Build, Stage
    from app.models.pipeline import Pipeline

    project_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Pipeline(
                id=uuid.uuid4(), project_id=project_id, name="p",
                default_branch="main", yaml_content=BAD_YAML,
                enabled=True, created_by=uuid.uuid4(),
                source_repo_url="https://example.com/r.git",
            )
        )
        await db.commit()

        repo = types.SimpleNamespace(
            project_id=project_id, id=uuid.uuid4(),
            repo_url="https://example.com/r.git",
        )
        event = types.SimpleNamespace(branch="main", commit_sha="abc123")

        ids = await _enqueue_matching_builds(db, repo, event)
        await db.commit()

    assert len(ids) == 1
    async with session_factory() as db:
        build = await db.get(Build, ids[0])
        assert build.status == "failed"
        stages = (await db.execute(select(Stage).where(Stage.build_id == ids[0]))).scalars().all()
        assert any(s.name == "validation" for s in stages)
