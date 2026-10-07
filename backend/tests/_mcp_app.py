"""An in-process app serving /mcp, and an MCP client for it."""
import contextlib
from types import SimpleNamespace

import httpx2
from fastapi import FastAPI
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

MCP_URL = "http://testserver/mcp"


def make_settings(**overrides):
    values = {
        "MEGOOCI_MCP_ENABLED": True,
        "MEGOOCI_MCP_ALLOWED_HOSTS": "testserver",
        "MEGOOCI_PUBLIC_API_URL": "http://localhost:8000",
        "MEGOOCI_PUBLIC_URL": "http://localhost:3000",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def build_app(session_factory, **settings_overrides):
    """A FastAPI app with the real REST router, an in-memory DB, and /mcp.

    Returns (app, mcp_app); mcp_app is None when MCP is disabled.
    """
    from fastapi import APIRouter

    from app.api.v1 import (
        artifacts, auth, builds, pipelines, project_repositories, projects, search,
    )
    from app.database import get_db
    from app.mcp import mount_mcp

    # Only the routers the MCP tools call, with the same prefixes as
    # app/api/v1/router.py. The full router is not used because it imports
    # optional heavy dependencies (litellm) that the test venv does not have.
    api = APIRouter()
    api.include_router(auth.router, prefix="/auth")
    api.include_router(projects.router, prefix="/projects")
    api.include_router(pipelines.router, prefix="/pipelines")
    api.include_router(builds.router, prefix="/builds")
    api.include_router(artifacts.router, prefix="")
    api.include_router(project_repositories.router, prefix="/projects/{project_id}/repositories")
    api.include_router(search.router, prefix="/search")

    app = FastAPI()
    app.include_router(api, prefix="/api/v1")

    async def _get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    mcp_app = mount_mcp(app, make_settings(**settings_overrides), session_factory)
    return app, mcp_app


@contextlib.asynccontextmanager
async def mcp_client(app, token):
    """An MCP client talking to *app* in-process as *token*."""
    http = httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        headers={"Authorization": f"Bearer {token}"},
    )
    async with http:
        async with Client(streamable_http_client(MCP_URL, http_client=http)) as client:
            yield client


def text_of(result) -> str:
    """The text of a tool result."""
    return "\n".join(block.text for block in result.content)
