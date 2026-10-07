"""Identity, project and pipeline tools map to the right REST calls."""
import uuid

import pytest

from app.mcp.client import ApiError
from tests._mcp_tools import FakeApi, run_tool

PID = "11111111-1111-1111-1111-111111111111"
LID = "22222222-2222-2222-2222-222222222222"


# ── identity ────────────────────────────────────────────────────────────

async def test_whoami_returns_identity_and_permissions():
    api = FakeApi({("GET", "/auth/me"): {
        "id": "u1", "email": "a@b.c", "name": "A", "role": "developer", "is_admin": False,
        "permissions": ["builds.read"], "auth_provider": "local", "created_at": "x",
    }})
    assert await run_tool("whoami", api) == {
        "id": "u1", "email": "a@b.c", "name": "A", "role": "developer",
        "is_admin": False, "permissions": ["builds.read"],
    }


async def test_search_maps_query_to_q():
    api = FakeApi({("GET", "/search"): {"query": "dep", "results": [{"id": "1", "type": "pipeline"}]}})
    assert await run_tool("search", api, query="dep") == [{"id": "1", "type": "pipeline"}]
    assert api.calls == [("GET", "/search", {"params": {"q": "dep", "limit": 5}})]


# ── projects ────────────────────────────────────────────────────────────

async def test_list_projects_returns_compact_rows_and_total():
    api = FakeApi({("GET", "/projects"): {"total": 9, "items": [{
        "id": PID, "name": "Web", "slug": "web", "description": None, "parent_id": None,
        "created_by": "u", "created_at": "t", "updated_at": None,
    }]}})
    assert await run_tool("list_projects", api, skip=5, limit=1) == {
        "total": 9,
        "items": [{"id": PID, "name": "Web", "slug": "web", "description": None, "parent_id": None}],
    }
    assert api.calls == [("GET", "/projects", {"params": {"skip": 5, "limit": 1}})]


async def test_list_project_repositories_uses_the_trailing_slash_route():
    api = FakeApi({("GET", f"/projects/{PID}/repositories/"): [{
        "id": "r1", "repo_url": "https://g/x.git", "default_branch": "main",
        "display_name": "x", "webhook_slug": "secret-ish", "connection_id": "c",
    }]})
    assert await run_tool("list_project_repositories", api, project_id=PID) == [
        {"id": "r1", "repo_url": "https://g/x.git", "default_branch": "main", "display_name": "x"}
    ]


async def test_create_project_sends_only_given_fields():
    api = FakeApi({("POST", "/projects"): {"id": PID}})
    await run_tool("create_project", api, name="Web")
    assert api.calls == [("POST", "/projects", {"json_body": {"name": "Web"}})]


async def test_update_project_requires_a_change():
    api = FakeApi()
    with pytest.raises(ApiError) as exc:
        await run_tool("update_project", api, project_id=PID)
    assert exc.value.status_code == 400
    assert api.calls == []


async def test_delete_project_never_sends_force():
    api = FakeApi()
    assert await run_tool("delete_project", api, project_id=PID) == {"deleted": True, "project_id": PID}
    assert api.calls == [("DELETE", f"/projects/{PID}", {})]


async def test_delete_project_conflict_explains_no_cascade():
    api = FakeApi(error=ApiError(409, "Cannot delete project: it still has 2 pipeline(s)."))
    with pytest.raises(ApiError) as exc:
        await run_tool("delete_project", api, project_id=PID)
    assert exc.value.status_code == 409
    assert "2 pipeline(s)" in exc.value.detail
    assert "web UI" in exc.value.detail


async def test_delete_project_other_errors_are_unchanged():
    api = FakeApi(error=ApiError(403, "Permission 'projects.manage' required for this project"))
    with pytest.raises(ApiError) as exc:
        await run_tool("delete_project", api, project_id=PID)
    assert exc.value.detail == "Permission 'projects.manage' required for this project"


# ── pipelines ───────────────────────────────────────────────────────────

async def test_list_pipelines_omits_yaml():
    api = FakeApi({("GET", "/pipelines"): {"total": 1, "items": [{
        "id": LID, "project_id": PID, "name": "deploy", "default_branch": "main",
        "enabled": True, "yaml_content": "name: big\n" * 500,
    }]}})
    result = await run_tool("list_pipelines", api, project_id=PID)
    assert result["items"] == [{"id": LID, "project_id": PID, "name": "deploy",
                                "default_branch": "main", "enabled": True}]
    assert api.calls[0][2] == {"params": {"project_id": PID, "skip": 0, "limit": 20}}


async def test_list_pipelines_without_project_sends_no_filter():
    api = FakeApi({("GET", "/pipelines"): {"total": 0, "items": []}})
    await run_tool("list_pipelines", api)
    assert api.calls[0][2]["params"]["project_id"] is None


async def test_validate_pipeline_yaml_posts_content():
    api = FakeApi({("POST", "/pipelines/validate"): {"valid": True, "errors": []}})
    assert await run_tool("validate_pipeline_yaml", api, yaml_content="name: x") == {"valid": True, "errors": []}
    assert api.calls == [("POST", "/pipelines/validate", {"json_body": {"yaml_content": "name: x"}})]


async def test_create_pipeline_serializes_ids_as_strings():
    api = FakeApi({("POST", "/pipelines"): {"id": LID}})
    await run_tool("create_pipeline", api, project_id=PID, name="deploy", yaml_content="name: x")
    assert api.calls == [("POST", "/pipelines", {"json_body": {
        "project_id": PID, "name": "deploy", "yaml_content": "name: x",
    }})]


async def test_update_pipeline_null_means_leave_unchanged():
    """Agents pass null for arguments they do not care about; that must not
    blank the column (name is NOT NULL, so it would be a server error)."""
    api = FakeApi({("PUT", f"/pipelines/{LID}"): {"id": LID}})
    await run_tool("update_pipeline", api, pipeline_id=LID, name=None, yaml_content=None, enabled=False)
    assert api.calls == [("PUT", f"/pipelines/{LID}", {"json_body": {"enabled": False}})]


async def test_update_pipeline_with_only_nulls_is_rejected():
    api = FakeApi()
    with pytest.raises(ApiError) as exc:
        await run_tool("update_pipeline", api, pipeline_id=LID, name=None)
    assert exc.value.status_code == 400 and api.calls == []


async def test_delete_pipeline_never_sends_force():
    api = FakeApi()
    await run_tool("delete_pipeline", api, pipeline_id=LID)
    assert api.calls == [("DELETE", f"/pipelines/{LID}", {})]


async def test_delete_pipeline_conflict_explains_no_cascade():
    api = FakeApi(error=ApiError(409, "Cannot delete pipeline: it still has 3 build(s)."))
    with pytest.raises(ApiError) as exc:
        await run_tool("delete_pipeline", api, pipeline_id=LID)
    assert "3 build(s)" in exc.value.detail and "web UI" in exc.value.detail


# ── arguments ───────────────────────────────────────────────────────────

async def test_ids_must_be_uuids():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await run_tool("get_project", FakeApi(), project_id="web")


async def test_uuid_objects_and_strings_are_both_accepted():
    api = FakeApi({("GET", f"/projects/{PID}"): {"id": PID}})
    await run_tool("get_project", api, project_id=uuid.UUID(PID))
    assert api.calls[0][1] == f"/projects/{PID}"


# ── review fixes ────────────────────────────────────────────────────────

# The exact 409 texts the REST handlers produce (projects.py / pipelines.py).
REST_PROJECT_409 = (
    "Cannot delete project: it still has 2 pipeline(s), 1 secret(s). Remove them "
    "first, or retry with ?force=true to cascade-delete everything in this project."
)
REST_PIPELINE_409 = (
    "Cannot delete pipeline: it still has 3 build(s). "
    "Retry with ?force=true to cascade-delete them."
)


async def test_blocked_project_delete_does_not_advertise_force():
    api = FakeApi(error=ApiError(409, REST_PROJECT_409))
    with pytest.raises(ApiError) as exc:
        await run_tool("delete_project", api, project_id=PID)
    detail = exc.value.detail
    assert "force" not in detail
    assert "2 pipeline(s), 1 secret(s)" in detail
    assert "web UI" in detail


async def test_blocked_pipeline_delete_does_not_advertise_force():
    api = FakeApi(error=ApiError(409, REST_PIPELINE_409))
    with pytest.raises(ApiError) as exc:
        await run_tool("delete_pipeline", api, pipeline_id=LID)
    detail = exc.value.detail
    assert "force" not in detail
    assert "3 build(s)" in detail
    assert "web UI" in detail


@pytest.mark.parametrize(
    "tool, id_field, id_value, field",
    [
        ("update_project", "project_id", PID, "name"),
        ("update_project", "project_id", PID, "description"),
        ("update_pipeline", "pipeline_id", LID, "name"),
        ("update_pipeline", "pipeline_id", LID, "yaml_content"),
        ("update_pipeline", "pipeline_id", LID, "default_branch"),
        ("update_pipeline", "pipeline_id", LID, "source_repo_url"),
    ],
)
async def test_update_tools_reject_empty_strings(tool, id_field, id_value, field):
    """An empty string must not blank a column: fields cannot be cleared through MCP."""
    from pydantic import ValidationError

    api = FakeApi()
    with pytest.raises(ValidationError):
        await run_tool(tool, api, **{id_field: id_value, field: ""})
    assert api.calls == []
