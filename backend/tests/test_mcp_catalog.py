"""Invariants that must hold across the whole tool catalog."""
from app.core.permissions import VALID_PERMISSIONS
from app.core.token_scopes import expand_scopes
from app.mcp.registry import visible_tools
from app.mcp.tools import ALL_TOOLS

VIEWER = {"projects.read", "pipelines.read", "builds.read", "artifacts.read"}


def test_catalog_has_22_uniquely_named_tools():
    names = [t.name for t in ALL_TOOLS]
    assert len(names) == 22
    assert len(set(names)) == 22


def test_catalog_only_requires_real_permissions():
    for tool in ALL_TOOLS:
        assert tool.required_permissions <= VALID_PERMISSIONS - {"admin"}, tool.name


def test_read_flag_matches_required_permission():
    """A tool is read-only exactly when it needs no *.manage permission."""
    for tool in ALL_TOOLS:
        needs_manage = any(p.endswith(".manage") for p in tool.required_permissions)
        assert tool.read_only is (not needs_manage), tool.name
        assert not (tool.read_only and tool.destructive), tool.name


def test_destructive_tools_are_exactly_the_deletes_and_cancel():
    assert {t.name for t in ALL_TOOLS if t.destructive} == {
        "delete_project", "delete_pipeline", "cancel_build",
    }


def test_read_only_scope_hides_every_write_tool():
    visible = visible_tools(ALL_TOOLS, expand_scopes(["read.only"]))
    assert visible and all(t.read_only for t in visible)


def test_coding_agent_token_owned_by_a_viewer_shows_only_read_tools():
    effective = expand_scopes(["coding.agent"]) & VIEWER
    visible = visible_tools(ALL_TOOLS, effective)
    assert visible and all(t.read_only for t in visible)


def test_coding_agent_scope_reaches_every_tool():
    visible = visible_tools(ALL_TOOLS, expand_scopes(["coding.agent"]))
    assert len(visible) == 22


def test_no_tool_accepts_a_force_argument():
    for tool in ALL_TOOLS:
        assert "force" not in tool.input_schema().get("properties", {}), tool.name


def test_every_input_schema_is_a_closed_object():
    for tool in ALL_TOOLS:
        schema = tool.input_schema()
        assert schema["type"] == "object", tool.name
        assert schema.get("additionalProperties") is False, tool.name
