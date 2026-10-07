"""An agent built before http_request existed fails the step without writing
any output. The server must explain that instead of showing an empty log."""

import os
import uuid
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import selectinload

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._rbac import build_inmemory_factory, seed_pipeline, seed_project


class FakeRedis:
    def __init__(self):
        self.published = []

    async def publish(self, channel, message):
        self.published.append((channel, message))


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _seed_failed_step(sf, *, step_type="http_request", agent_output=None):
    """A failed step that already has the 'Dispatching to agent' system line."""
    from app.models.build import Build, LogChunk, Stage, Step

    ids = {"build": uuid.uuid4(), "stage": uuid.uuid4(), "step": uuid.uuid4()}
    async with sf() as db:
        project = await seed_project(db, "P")
        pipeline = await seed_pipeline(db, project)
        db.add(Build(id=ids["build"], pipeline_id=pipeline, number=1, status="running",
                     trigger_type="manual"))
        await db.flush()
        db.add(Stage(id=ids["stage"], build_id=ids["build"], name="notify",
                     status="running", sort_order=0))
        await db.flush()
        db.add(Step(id=ids["step"], stage_id=ids["stage"], name="announce",
                    step_type=step_type, status="failed", exit_code=1, sort_order=0,
                    config_json={"url": "https://example.com/x"}))
        await db.flush()
        now = datetime.now(timezone.utc)
        db.add(LogChunk(id=uuid.uuid4(), step_id=ids["step"], seq=1, timestamp=now,
                        stream="system", content="→ Dispatching to agent\n"))
        if agent_output:
            db.add(LogChunk(id=uuid.uuid4(), step_id=ids["step"], seq=2, timestamp=now,
                            stream="stderr", content=agent_output))
        await db.commit()
    return ids


async def _system_lines(sf, step_id):
    from app.models.build import LogChunk

    async with sf() as db:
        rows = await db.execute(
            select(LogChunk).where(LogChunk.step_id == step_id, LogChunk.stream == "system")
            .order_by(LogChunk.seq)
        )
        return [c.content for c in rows.scalars()]


async def test_silent_http_request_failure_gets_an_explanation(sf):
    from app.models.build import Step
    from app.services.build_executor import _hint_if_agent_gave_no_output

    ids = await _seed_failed_step(sf)
    redis = FakeRedis()
    async with sf() as db:
        step = await db.get(Step, ids["step"])
        await _hint_if_agent_gave_no_output(step, db, redis, "build:x:logs")

    lines = await _system_lines(sf, ids["step"])
    assert len(lines) == 2
    assert "http_request" in lines[1]
    assert "update the agent" in lines[1].lower()
    assert len(redis.published) == 1


async def test_no_hint_when_the_agent_reported_why_it_failed(sf):
    from app.models.build import Step
    from app.services.build_executor import _hint_if_agent_gave_no_output

    ids = await _seed_failed_step(sf, agent_output="http_request: unexpected status 500 (expected 2xx)\n")
    redis = FakeRedis()
    async with sf() as db:
        step = await db.get(Step, ids["step"])
        await _hint_if_agent_gave_no_output(step, db, redis, "build:x:logs")

    assert len(await _system_lines(sf, ids["step"])) == 1
    assert redis.published == []


async def test_no_hint_for_step_types_every_agent_knows(sf):
    from app.models.build import Step
    from app.services.build_executor import _hint_if_agent_gave_no_output

    ids = await _seed_failed_step(sf, step_type="run")
    async with sf() as db:
        step = await db.get(Step, ids["step"])
        await _hint_if_agent_gave_no_output(step, db, FakeRedis(), "build:x:logs")

    assert len(await _system_lines(sf, ids["step"])) == 1


async def test_execute_step_adds_the_hint_after_a_silent_agent_failure(sf, monkeypatch):
    """Wiring: the executor calls the hint when an agent-run step comes back failed."""
    from app.models.build import Build, Stage, Step
    from app.services import build_executor

    ids = await _seed_failed_step(sf)
    agent_id = uuid.uuid4()

    async def fake_dispatch(step, stage_name, build_id, db, **kwargs):
        return {"status": "failed", "exit_code": 1}, agent_id

    monkeypatch.setattr(build_executor, "_try_dispatch_to_agent", fake_dispatch)

    async with sf() as db:
        build = await db.get(Build, ids["build"])
        stage = (await db.execute(
            select(Stage).where(Stage.id == ids["stage"]).options(selectinload(Stage.steps))
        )).scalar_one()
        step = await db.get(Step, ids["step"])
        result = await build_executor._execute_step(
            step, stage, build, {}, {}, {}, db, FakeRedis(), "build:x:logs",
        )

    assert result.status == "failed"
    lines = await _system_lines(sf, ids["step"])
    assert any("update the agent" in line.lower() for line in lines), lines
