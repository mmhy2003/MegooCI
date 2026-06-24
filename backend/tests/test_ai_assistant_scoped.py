"""Tests for project-scoped access on the AI assistant endpoints (FIX 3).

The endpoints now require ``pipelines.manage`` scoped to the request's project
instead of a global grant. We exercise the ``_check_ai_access`` helper directly
(the LLM call is never made) so no external network is needed.

Scenarios:
  - Scoped developer for the project is not 403'd.
  - User with NO project assignment gets 403 when project_id is provided.
  - User with no global role gets 403 when no project context is given.
  - Admin always passes.
  - pipeline_id (no project_id) resolves to project and checks scope.
"""
import os, sys, uuid
from unittest.mock import MagicMock
import pytest, pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is an optional dep not installed in the test venv; stub it before
# importing the module under test so the module-level `import litellm` succeeds.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from tests._rbac import (
    build_inmemory_factory,
    make_role,
    make_user,
    seed_project,
    seed_pipeline,
)

MANAGE = ["pipelines.manage", "pipelines.read"]
VIEW = ["pipelines.read"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def ai_scenario(sf):
    async with sf() as db:
        proj_a = await seed_project(db, "A")
        proj_b = await seed_project(db, "B")
        pipeline_id = await seed_pipeline(db, proj_a)
        await db.commit()
    return sf, proj_a, proj_b, pipeline_id


# ---------------------------------------------------------------------------
# Tests — exercising _check_ai_access directly
# ---------------------------------------------------------------------------

async def test_scoped_developer_not_403_with_project_id(ai_scenario):
    """A developer scoped to project A can call the AI endpoint for project A."""
    from app.api.v1.ai_assistant import _check_ai_access, AssistantRequest

    sf, proj_a, proj_b, pipeline_id = ai_scenario
    user = make_user(project_roles=[(proj_a, make_role("dev", MANAGE))])
    body = AssistantRequest(prompt="help", project_id=str(proj_a))

    async with sf() as db:
        # Should NOT raise.
        await _check_ai_access(body, db, user)


async def test_non_member_403_with_project_id(ai_scenario):
    """A user with project B role gets 403 for a project A request."""
    from app.api.v1.ai_assistant import _check_ai_access, AssistantRequest

    sf, proj_a, proj_b, pipeline_id = ai_scenario
    user = make_user(project_roles=[(proj_b, make_role("dev", MANAGE))])
    body = AssistantRequest(prompt="help", project_id=str(proj_a))

    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await _check_ai_access(body, db, user)
    assert exc.value.status_code == 403


async def test_admin_always_passes(ai_scenario):
    """Admin passes regardless of project assignment."""
    from app.api.v1.ai_assistant import _check_ai_access, AssistantRequest

    sf, proj_a, proj_b, pipeline_id = ai_scenario
    admin = make_user(is_admin=True)
    body = AssistantRequest(prompt="help", project_id=str(proj_a))

    async with sf() as db:
        # Should NOT raise.
        await _check_ai_access(body, db, admin)


async def test_no_project_context_requires_global(ai_scenario):
    """Without a project_id/pipeline_id, a project-only developer gets 403."""
    from app.api.v1.ai_assistant import _check_ai_access, AssistantRequest

    sf, proj_a, proj_b, pipeline_id = ai_scenario
    # User only has a project-scoped role, not a global one.
    user = make_user(project_roles=[(proj_a, make_role("dev", MANAGE))])
    body = AssistantRequest(prompt="help")  # no project context

    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await _check_ai_access(body, db, user)
    assert exc.value.status_code == 403


async def test_global_user_passes_without_project_context(ai_scenario):
    """A user with a global pipelines.manage role passes with no project context."""
    from app.api.v1.ai_assistant import _check_ai_access, AssistantRequest

    sf, proj_a, proj_b, pipeline_id = ai_scenario
    global_role = make_role("global-dev", MANAGE)
    user = make_user(global_role=global_role)
    body = AssistantRequest(prompt="help")  # no project context

    async with sf() as db:
        # Should NOT raise.
        await _check_ai_access(body, db, user)


async def test_pipeline_id_resolves_to_project(ai_scenario):
    """Providing pipeline_id (no project_id) resolves to the pipeline's project."""
    from app.api.v1.ai_assistant import _check_ai_access, AssistantRequest

    sf, proj_a, proj_b, pipeline_id = ai_scenario

    # User is a member of project A (where the pipeline lives) — should pass.
    user_a = make_user(project_roles=[(proj_a, make_role("dev", MANAGE))])
    body = AssistantRequest(prompt="help", pipeline_id=str(pipeline_id))

    async with sf() as db:
        await _check_ai_access(body, db, user_a)

    # User is only a member of project B — should 403.
    user_b = make_user(project_roles=[(proj_b, make_role("dev", MANAGE))])
    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await _check_ai_access(body, db, user_b)
    assert exc.value.status_code == 403
