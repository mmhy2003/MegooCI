"""MCP endpoint for coding agents.

Agents authenticate with a Personal Access Token and act as its owner. Every
tool calls the REST API in-process, so roles and token scopes apply unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from starlette.applications import Starlette
from starlette.routing import Route

from app.mcp.server import MCP_PATH, McpApp, create_mcp_app

__all__ = ["MCP_PATH", "McpApp", "create_mcp_app", "mount_mcp"]


def mount_mcp(
    app: Starlette,
    settings: Any | None = None,
    session_factory: Callable[[], Any] | None = None,
) -> McpApp | None:
    """Serve the MCP endpoint from *app* at exactly ``/mcp``.

    Returns None (and mounts nothing) when MEGOOCI_MCP_ENABLED is false. The
    caller must enter ``McpApp.run()`` in the app's lifespan.
    """
    if settings is None:
        from app.config import get_settings

        settings = get_settings()
    if not settings.MEGOOCI_MCP_ENABLED:
        return None

    mcp_app = create_mcp_app(app, settings, session_factory)
    # A Route (not a Mount) so the path is exactly /mcp with no redirect.
    # POST only: the transport would answer GET with an event stream that
    # stays open and so outlives a revoked token. Other methods get 405.
    app.router.routes.append(Route(MCP_PATH, endpoint=mcp_app.asgi, methods=["POST"]))
    return mcp_app
