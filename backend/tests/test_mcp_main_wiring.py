"""The real application (app.main) serves /mcp and runs its session manager."""
import os
import sys
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is an optional dep not installed in the test venv; app.main imports
# the full router, which imports it at module level.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from tests._mcp import seed_member, seed_token
from tests._mcp_app import mcp_client
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _noop(*args, **kwargs):
    return None


async def test_real_app_serves_mcp_under_its_own_lifespan(sf, monkeypatch):
    """One test on purpose: the app's session manager can be entered once per process."""
    import app.main as main

    if main.mcp_app is None:
        pytest.skip("MEGOOCI_MCP_ENABLED is false in this environment")

    # Startup work that needs Postgres / Meilisearch is not under test here.
    monkeypatch.setattr(main, "init_db", _noop)
    monkeypatch.setattr("app.services.seed.seed_admin_user", _noop)
    monkeypatch.setattr("app.services.search.ensure_indexes", _noop)
    monkeypatch.setattr("app.services.search.sync_all", _noop)
    # Authenticate against the in-memory database instead of Postgres.
    monkeypatch.setattr(main.mcp_app.asgi, "_session_factory", sf)

    async with sf() as db:
        uid = await seed_member(db, ["projects.read", "builds.read"])
        token = await seed_token(db, uid)
        await db.commit()

    async with main.app.router.lifespan_context(main.app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app), base_url="http://localhost:8000"
        ) as http:
            unauthenticated = await http.post("/mcp", json={})
            health = await http.get("/health")
        async with mcp_client(main.app, token, url="http://localhost:8000/mcp") as client:
            names = {tool.name for tool in (await client.list_tools()).tools}

    assert unauthenticated.status_code == 401
    assert health.status_code == 200
    assert {"whoami", "list_projects", "get_build_logs"} <= names
    assert "trigger_build" not in names
