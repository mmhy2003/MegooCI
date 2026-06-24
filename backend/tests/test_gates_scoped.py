"""Tests for project-scoped access on the wait_input gate endpoint (FIX 2).

The endpoint now requires ``builds.manage`` scoped to the build's project
instead of a global ``builds.manage`` permission. We test:
  - A developer assigned to the build's project can call the endpoint (no 403).
  - A user with no project assignment gets 403.
  - An admin always gets through.

Redis and the actual Redis SET are stubbed out so no network is needed.
"""
import os, uuid
import pytest, pytest_asyncio
from fastapi import HTTPException
from unittest.mock import AsyncMock, patch

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")
from tests._rbac import (
    build_inmemory_factory,
    make_role,
    make_user,
    seed_project,
    seed_pipeline,
    seed_build,
)

DEV = ["pipelines.read", "builds.read", "builds.manage"]
VIEW = ["pipelines.read", "builds.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _seed_wait_input_step(db, pipeline_id) -> tuple[uuid.UUID, uuid.UUID]:
    """Seed a build → stage → wait_input step. Returns (build_id, step_id)."""
    from app.models.build import Build, Stage, Step

    build = Build(
        id=uuid.uuid4(),
        pipeline_id=pipeline_id,
        number=42,
        status="running",
        trigger_type="manual",
    )
    db.add(build)
    await db.flush()

    stage = Stage(
        id=uuid.uuid4(),
        build_id=build.id,
        name="approval",
        status="running",
        sort_order=0,
    )
    db.add(stage)
    await db.flush()

    step = Step(
        id=uuid.uuid4(),
        stage_id=stage.id,
        name="wait for approval",
        step_type="wait_input",
        status="running",
        sort_order=0,
    )
    db.add(step)
    await db.flush()
    await db.commit()

    return build.id, step.id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def gate_scenario(sf):
    """Seed two projects, one with a pipeline+build+step, one without."""
    async with sf() as db:
        proj_a = await seed_project(db, "A")
        proj_b = await seed_project(db, "B")
        pipeline_id = await seed_pipeline(db, proj_a)
        _, step_id = await _seed_wait_input_step(db, pipeline_id)
    return sf, proj_a, proj_b, step_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_input_gate_member_can_approve(gate_scenario):
    """A developer in project A can approve the wait_input step."""
    sf, proj_a, proj_b, step_id = gate_scenario
    from app.api.v1.gates import resolve_input_gate, InputGatePayload

    user = make_user(project_roles=[(proj_a, make_role("dev", DEV))])

    _fake_redis = AsyncMock()
    _fake_redis.set = AsyncMock(return_value=True)
    _fake_redis.aclose = AsyncMock()

    with patch("app.api.v1.gates.aioredis.from_url", return_value=_fake_redis):
        async with sf() as db:
            result = await resolve_input_gate(
                step_id=step_id,
                body=InputGatePayload(approved=True),
                db=db,
                current_user=user,
            )

    assert result["status"] == "approved"
    assert result["step_id"] == str(step_id)


async def test_input_gate_non_member_gets_403(gate_scenario):
    """A user with no project assignment gets 403."""
    sf, proj_a, proj_b, step_id = gate_scenario
    from app.api.v1.gates import resolve_input_gate, InputGatePayload

    # User has DEV role on proj_b, NOT proj_a (where the build lives).
    user = make_user(project_roles=[(proj_b, make_role("dev", DEV))])

    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await resolve_input_gate(
                step_id=step_id,
                body=InputGatePayload(approved=False),
                db=db,
                current_user=user,
            )

    assert exc.value.status_code == 403


async def test_input_gate_admin_can_approve(gate_scenario):
    """An admin user can always approve regardless of project assignment."""
    sf, proj_a, proj_b, step_id = gate_scenario
    from app.api.v1.gates import resolve_input_gate, InputGatePayload

    admin = make_user(is_admin=True)

    _fake_redis = AsyncMock()
    _fake_redis.set = AsyncMock(return_value=True)
    _fake_redis.aclose = AsyncMock()

    with patch("app.api.v1.gates.aioredis.from_url", return_value=_fake_redis):
        async with sf() as db:
            result = await resolve_input_gate(
                step_id=step_id,
                body=InputGatePayload(approved=True),
                db=db,
                current_user=admin,
            )

    assert result["status"] == "approved"


async def test_input_gate_viewer_without_builds_manage_gets_403(gate_scenario):
    """A project member with only builds.read (no builds.manage) gets 403."""
    sf, proj_a, proj_b, step_id = gate_scenario
    from app.api.v1.gates import resolve_input_gate, InputGatePayload

    viewer = make_user(project_roles=[(proj_a, make_role("viewer", VIEW))])

    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await resolve_input_gate(
                step_id=step_id,
                body=InputGatePayload(approved=True),
                db=db,
                current_user=viewer,
            )

    assert exc.value.status_code == 403
