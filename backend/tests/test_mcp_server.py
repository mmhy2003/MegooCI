"""End-to-end MCP behavior: a real MCP client against the app in-process."""
import json
import os

import httpx
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._mcp import seed_member, seed_token
from tests._mcp_app import build_app, mcp_client, text_of
from tests._rbac import build_inmemory_factory, seed_build, seed_pipeline, seed_project

DEV = ["projects.read", "projects.manage", "pipelines.read", "pipelines.manage",
       "builds.read", "builds.manage", "artifacts.read"]
VIEW = ["projects.read", "pipelines.read", "builds.read", "artifacts.read"]
WRITE_TOOLS = {
    "create_project", "update_project", "delete_project",
    "create_pipeline", "update_pipeline", "delete_pipeline",
    "trigger_build", "cancel_build", "retry_build",
}


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


def raw_http(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_no_token_is_401(sf):
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), raw_http(app) as http:
        response = await http.post("/mcp", json={})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


async def test_jwt_is_401(sf):
    from app.core.security import create_access_token

    async with sf() as db:
        uid = await seed_member(db, DEV)
        await db.commit()
    app, mcp_app = build_app(sf)
    jwt = create_access_token({"sub": str(uid)})
    async with mcp_app.run(), raw_http(app) as http:
        response = await http.post("/mcp", json={}, headers={"Authorization": f"Bearer {jwt}"})
    assert response.status_code == 401


async def test_revoked_token_is_401(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, is_active=False)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), raw_http(app) as http:
        response = await http.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_inactive_owner_is_401(sf):
    from app.models.user import User

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        (await db.get(User, uid)).is_active = False
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), raw_http(app) as http:
        response = await http.post("/mcp", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


async def test_malformed_authorization_is_401(sf):
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), raw_http(app) as http:
        for value in ("Basic abc", "Bearer", "Bearer    ", "megci_pat_without_scheme"):
            response = await http.post("/mcp", json={}, headers={"Authorization": value})
            assert response.status_code == 401, value


async def test_unknown_host_is_421(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), raw_http(app) as http:
        response = await http.post(
            "/mcp", json={},
            headers={"Authorization": f"Bearer {token}", "Host": "evil.example"},
        )
    assert response.status_code == 421


async def test_disabled_returns_404(sf):
    app, mcp_app = build_app(sf, MEGOOCI_MCP_ENABLED=False)
    assert mcp_app is None
    async with raw_http(app) as http:
        response = await http.post("/mcp", json={})
    assert response.status_code == 404


async def test_full_access_developer_sees_all_22_tools(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == 22
    by_name = {t.name: t for t in tools}
    assert by_name["list_projects"].annotations.read_only_hint is True
    assert by_name["delete_pipeline"].annotations.destructive_hint is True
    assert "project_id" in by_name["get_project"].input_schema["properties"]


async def test_read_only_token_sees_no_write_tools(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, scopes=["read.only"])
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names and not (names & WRITE_TOOLS)
    assert {"whoami", "list_projects", "get_build_logs"} <= names


async def test_hidden_tool_call_is_unknown_tool(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, scopes=["read.only"])
        pid = await seed_project(db, "A")
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        result = await client.call_tool("delete_project", {"project_id": str(pid)})
    assert result.is_error is True
    assert text_of(result) == "Unknown tool: delete_project"
    async with sf() as db:
        from app.models.project import Project
        assert await db.get(Project, pid) is not None


async def test_invalid_arguments_are_a_tool_error(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        result = await client.call_tool("get_project", {"project_id": "not-a-uuid"})
        extra = await client.call_tool("list_projects", {"force": True})
    assert result.is_error is True and "project_id" in text_of(result)
    assert extra.is_error is True and "force" in text_of(extra)


async def test_whoami_reflects_token_scope(sf):
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, scopes=["read.only"])
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        result = await client.call_tool("whoami", {})
    me = json.loads(text_of(result))
    assert me["id"] == str(uid)
    assert "projects.read" in me["permissions"]
    assert "projects.manage" not in me["permissions"]


async def test_project_scoped_user_sees_only_their_project(sf):
    async with sf() as db:
        a = await seed_project(db, "A")
        b = await seed_project(db, "B")
        pipeline_b = await seed_pipeline(db, b)
        uid = await seed_member(db, VIEW, project_id=a)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        listed = await client.call_tool("list_projects", {})
        other = await client.call_tool("get_pipeline", {"pipeline_id": str(pipeline_b)})
    body = json.loads(text_of(listed))
    assert body["total"] == 1
    assert [p["id"] for p in body["items"]] == [str(a)]
    assert other.is_error is True
    assert "pipelines.read" in text_of(other)


async def test_trigger_build_without_permission_names_the_permission(sf):
    async with sf() as db:
        a = await seed_project(db, "A")
        b = await seed_project(db, "B")
        pipeline_b = await seed_pipeline(db, b)
        # builds.manage on A only: the tool is visible, but B is off limits.
        uid = await seed_member(db, DEV, project_id=a)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        result = await client.call_tool("trigger_build", {"pipeline_id": str(pipeline_b)})
    assert result.is_error is True
    assert "builds.manage" in text_of(result)


async def test_delete_pipeline_with_builds_is_refused(sf):
    from app.models.pipeline import Pipeline

    async with sf() as db:
        a = await seed_project(db, "A")
        pipeline = await seed_pipeline(db, a)
        await seed_build(db, pipeline)
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        result = await client.call_tool("delete_pipeline", {"pipeline_id": str(pipeline)})
    assert result.is_error is True
    message = text_of(result)
    assert "1 build(s)" in message
    assert "web UI" in message
    assert "force" not in message
    async with sf() as db:
        assert await db.get(Pipeline, pipeline) is not None


async def test_pipeline_round_trip(sf):
    async with sf() as db:
        a = await seed_project(db, "A")
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, scopes=["coding.agent"])
        await db.commit()
    app, mcp_app = build_app(sf)
    yaml = "name: demo\nstages:\n  - name: build\n    steps:\n      - run: echo hi\n"
    async with mcp_app.run(), mcp_client(app, token) as client:
        created = await client.call_tool(
            "create_pipeline", {"project_id": str(a), "name": "demo", "yaml_content": yaml}
        )
        pipeline_id = json.loads(text_of(created))["id"]
        updated = await client.call_tool(
            "update_pipeline", {"pipeline_id": pipeline_id, "enabled": False}
        )
        listed = await client.call_tool("list_pipelines", {"project_id": str(a)})
        fetched = await client.call_tool("get_pipeline", {"pipeline_id": pipeline_id})
        empty = await client.call_tool("update_pipeline", {"pipeline_id": pipeline_id})
        deleted = await client.call_tool("delete_pipeline", {"pipeline_id": pipeline_id})
    assert created.is_error is False
    assert json.loads(text_of(updated))["enabled"] is False
    rows = json.loads(text_of(listed))["items"]
    assert rows == [{"id": pipeline_id, "project_id": str(a), "name": "demo",
                     "default_branch": "main", "enabled": False}]
    assert json.loads(text_of(fetched))["yaml_content"] == yaml
    assert empty.is_error is True and "at least one field" in text_of(empty)
    assert json.loads(text_of(deleted)) == {"deleted": True, "pipeline_id": pipeline_id}


async def test_get_build_logs_end_to_end(sf):
    import uuid
    from datetime import datetime, timezone

    from app.models.build import LogChunk, Stage, Step

    async with sf() as db:
        a = await seed_project(db, "A")
        pipeline = await seed_pipeline(db, a)
        build = await seed_build(db, pipeline, status="failed")
        stage = Stage(id=uuid.uuid4(), build_id=build, name="test", status="failed", sort_order=0)
        db.add(stage)
        await db.flush()
        step = Step(id=uuid.uuid4(), stage_id=stage.id, name="pytest", status="failed",
                    exit_code=1, sort_order=0)
        db.add(step)
        await db.flush()
        db.add(LogChunk(id=uuid.uuid4(), step_id=step.id, seq=0,
                        timestamp=datetime.now(timezone.utc), content="boom\nexit 1\n"))
        uid = await seed_member(db, VIEW)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    async with mcp_app.run(), mcp_client(app, token) as client:
        logs = await client.call_tool("get_build_logs", {"build_id": str(build)})
        detail = await client.call_tool("get_build", {"build_id": str(build)})
    text = text_of(logs)
    assert logs.is_error is False
    assert "== test / pytest ==" in text and "boom" in text
    step_row = json.loads(text_of(detail))["stages"][0]["steps"][0]
    assert step_row["name"] == "pytest" and step_row["exit_code"] == 1
    assert "config_json" not in step_row


async def test_unexpected_failure_is_a_generic_error(sf, monkeypatch):
    """An unexpected exception must not leak internals to the agent."""
    from app.mcp.client import ApiClient

    async def boom(self, method, path, **kwargs):
        raise RuntimeError("secret internals: db password")

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    monkeypatch.setattr(ApiClient, "request", boom)
    async with mcp_app.run(), mcp_client(app, token) as client:
        result = await client.call_tool("whoami", {})
    assert result.is_error is True
    assert text_of(result) == "MegooCI internal error"


async def test_tool_call_is_logged_without_its_arguments(sf, caplog):
    import logging

    async with sf() as db:
        a = await seed_project(db, "A")
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    with caplog.at_level(logging.INFO, logger="app.mcp.server"):
        async with mcp_app.run(), mcp_client(app, token) as client:
            await client.call_tool(
                "create_pipeline",
                {"project_id": str(a), "name": "demo", "yaml_content": "name: TOP-SECRET-YAML"},
            )
    lines = [r.getMessage() for r in caplog.records if r.name == "app.mcp.server"]
    assert any(
        "tool=create_pipeline" in line and f"user={uid}" in line and "status=201" in line
        for line in lines
    )
    assert not any("TOP-SECRET-YAML" in line for line in lines)


async def test_only_post_is_served(sf):
    """GET would open an event stream that outlives token revocation."""
    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    app, mcp_app = build_app(sf)
    headers = {"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}
    async with mcp_app.run(), raw_http(app) as http:
        got = await http.get("/mcp", headers=headers)
        deleted = await http.delete("/mcp", headers=headers)
    assert got.status_code == 405
    assert deleted.status_code == 405
