"""record_pipeline_validation_failure attaches a visible failed stage to a build."""

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

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for model in (Build, Stage, Step, LogChunk):
            await conn.run_sync(lambda c, m=model: m.__table__.create(c))
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


def test_format_validation_errors_includes_line():
    from app.services.build_validation import format_validation_errors
    from app.services.pipeline_compiler import PipelineError

    text = format_validation_errors(
        [
            PipelineError(message="bad indent", line=7, column=3),
            PipelineError(message="missing name", line=12),
            PipelineError(message="no line here"),
        ]
    )
    assert "Line 7, col 3: bad indent" in text
    assert "Line 12: missing name" in text
    assert "no line here" in text


async def test_records_failed_validation_stage(session_factory):
    from app.models.build import Build, Stage, Step
    from app.services.build_validation import record_pipeline_validation_failure
    from app.services.pipeline_compiler import PipelineError

    build_id = uuid.uuid4()
    async with session_factory() as db:
        build = Build(
            id=build_id, pipeline_id=uuid.uuid4(), number=1,
            status="pending", trigger_type="push",
        )
        db.add(build)
        await db.flush()

        await record_pipeline_validation_failure(
            db, build, [PipelineError(message="bad indent", line=7, column=3)]
        )
        await db.commit()

    async with session_factory() as db:
        reloaded = await db.get(Build, build_id)
        assert reloaded.status == "failed"
        assert reloaded.finished_at is not None

        stage = (await db.execute(select(Stage))).scalar_one()
        assert stage.name == "validation"
        assert stage.status == "failed"

        step = (await db.execute(select(Step))).scalar_one()
        assert step.name == "yaml-check"
        assert step.status == "failed"

    from app.models.build import LogChunk

    async with session_factory() as db:
        chunk = (await db.execute(select(LogChunk))).scalar_one()
        assert "Line 7, col 3: bad indent" in chunk.content
