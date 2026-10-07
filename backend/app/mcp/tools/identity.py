"""Identity and discovery tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from app.mcp.client import ApiClient
from app.mcp.registry import NoInput, ToolInput, ToolSpec
from app.mcp.tools._common import pick

_ME_KEYS = ("id", "email", "name", "role", "is_admin", "permissions")
_ANY_READ = frozenset({"projects.read", "pipelines.read", "builds.read", "artifacts.read"})


async def _whoami(api: ApiClient, args: NoInput) -> dict[str, Any]:
    return pick(await api.get("/auth/me"), _ME_KEYS)


class SearchInput(ToolInput):
    query: str = Field(min_length=1, max_length=200, description="Text to search for.")
    limit: int = Field(5, ge=1, le=20, description="Maximum results per kind.")


async def _search(api: ApiClient, args: SearchInput) -> list[dict[str, Any]]:
    body = await api.get("/search", params={"q": args.query, "limit": args.limit})
    return body["results"]


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="whoami",
        description=(
            "Show which MegooCI user this token acts as, their role, and the "
            "permissions the token actually has. Call this first when a tool "
            "reports a missing permission."
        ),
        input_model=NoInput,
        handler=_whoami,
        read_only=True,
    ),
    ToolSpec(
        name="search",
        description=(
            "Search projects, pipelines, builds and artifacts by text. Each hit "
            "has a `type` and an `id`; use the id with the other tools."
        ),
        input_model=SearchInput,
        handler=_search,
        required_permissions=_ANY_READ,
        read_only=True,
    ),
)
