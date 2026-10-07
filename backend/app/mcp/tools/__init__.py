"""The MCP tool catalog."""

from app.mcp.registry import ToolSpec
from app.mcp.tools import identity, pipelines, projects

ALL_TOOLS: tuple[ToolSpec, ...] = (
    *identity.TOOLS,
    *projects.TOOLS,
    *pipelines.TOOLS,
)
