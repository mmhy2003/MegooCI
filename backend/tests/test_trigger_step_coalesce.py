"""trigger_pipeline targeting a pipeline that already has a queued run coalesces
into it (no second pending build)."""

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
    from app.models.build import Build, Stage, Step
    from app.models.pipeline import Pipeline

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Pipeline, Build, Stage, Step):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


GOOD_YAML = "name: c\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"


async def test_trigger_step_coalesces(session_factory, monkeypatch):
    monkeypatch.setattr(
        "app.services.step_actions.trigger.run_build.delay", lambda *a, **k: None)

    from app.models.build import Build
    from app.models.pipeline import Pipeline
    from app.services.build_concurrency import create_or_coalesce_build
    from app.services.step_actions.base import StepContext, StepResult
    from app.services.step_actions.trigger import TriggerPipelineHandler

    target_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(Pipeline(id=target_id, project_id=uuid.uuid4(), name="c",
                        default_branch="main", yaml_content=GOOD_YAML, enabled=True,
                        created_by=uuid.uuid4()))
        await db.commit()

        # Pre-existing queued run for the target.
        await create_or_coalesce_build(
            db, pipeline_id=target_id, default_branch="main", branch="main",
            commit_sha="aaa", params=None, triggered_by=None, trigger_type="manual")
        await db.commit()

        ctx = StepContext(build_id=uuid.uuid4(), step_id=uuid.uuid4(),
                          step_name="trigger", stage_name="deploy",
                          pipeline_id=uuid.uuid4(), project_id=uuid.uuid4())
        handler = TriggerPipelineHandler()
        results = []
        async for item in handler.execute(
            {"pipeline": str(target_id), "wait": False}, ctx, db):
            results.append(item)

        final = [r for r in results if isinstance(r, StepResult)][-1]
        assert final.status == "success"   # step still succeeds (run is queued)
        count = await db.scalar(select(func.count()).select_from(Build).where(
            Build.pipeline_id == target_id, Build.status == "pending"))
        assert count == 1                  # coalesced — still one pending
