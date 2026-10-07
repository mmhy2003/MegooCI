"""The MCP tool catalog."""

from app.mcp.registry import ToolSpec
from app.mcp.tools import artifacts, builds, identity, pipelines, projects

ALL_TOOLS: tuple[ToolSpec, ...] = (
    *identity.TOOLS,
    *projects.TOOLS,
    *pipelines.TOOLS,
    *builds.TOOLS,
    *artifacts.TOOLS,
)
