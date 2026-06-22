"""trigger_pipeline targeting a pipeline with invalid YAML fails the step and
records a failed child build."""

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


BAD_YAML = "name: child\nstages:\n  - steps:\n      - run: echo hi\n"  # missing name


async def test_trigger_step_fails_on_invalid_target_yaml(session_factory):
    from app.models.build import Build, Stage
    from app.models.pipeline import Pipeline
    from app.services.step_actions.base import StepContext, StepResult
    from app.services.step_actions.trigger import TriggerPipelineHandler

    target_id = uuid.uuid4()
    async with session_factory() as db:
        db.add(
            Pipeline(
                id=target_id, project_id=uuid.uuid4(), name="child",
                default_branch="main", yaml_content=BAD_YAML,
                enabled=True, created_by=uuid.uuid4(),
            )
        )
        await db.commit()

        # The invalid-YAML path returns before reading ctx, but build a real
        # StepContext (all fields required, no defaults) so nothing is left to chance.
        ctx = StepContext(
            build_id=uuid.uuid4(), step_id=uuid.uuid4(), step_name="trigger",
            stage_name="deploy", pipeline_id=uuid.uuid4(), project_id=uuid.uuid4(),
        )
        handler = TriggerPipelineHandler()

        results = []
        async for item in handler.execute({"pipeline": str(target_id)}, ctx, db):
            results.append(item)

        final = [r for r in results if isinstance(r, StepResult)][-1]
        assert final.status == "failed"

        builds = (await db.execute(select(Build))).scalars().all()
        assert len(builds) == 1 and builds[0].status == "failed"
        stages = (await db.execute(select(Stage))).scalars().all()
        assert any(s.name == "validation" for s in stages)
