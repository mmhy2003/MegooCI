"""Tool visibility rules."""
from app.mcp.registry import NoInput, ToolSpec, is_visible, visible_tools


async def _noop(api, args):
    return None


def _spec(name, perms=()):
    return ToolSpec(name=name, description="d", input_model=NoInput, handler=_noop,
                    required_permissions=frozenset(perms))


def test_tool_without_requirement_is_visible_to_everyone():
    assert is_visible(_spec("whoami"), set())


def test_requirement_is_any_of():
    spec = _spec("search", {"projects.read", "builds.read"})
    assert is_visible(spec, {"builds.read"})
    assert not is_visible(spec, {"secrets.read"})


def test_admin_sentinel_sees_everything():
    assert is_visible(_spec("x", {"builds.manage"}), {"admin"})


def test_visible_tools_keeps_catalog_order():
    tools = [_spec("a", {"p.read"}), _spec("b", {"p.manage"}), _spec("c", {"p.read"})]
    assert [t.name for t in visible_tools(tools, {"p.read"})] == ["a", "c"]
