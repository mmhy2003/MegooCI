"""Tool registry: what an MCP tool is, and which tools a caller may see."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from app.mcp.client import ApiClient

ToolHandler = Callable[["ApiClient", Any], Awaitable[Any]]


class ToolInput(BaseModel):
    """Base class for tool arguments. Unknown arguments are rejected."""

    model_config = ConfigDict(extra="forbid")


class NoInput(ToolInput):
    """A tool that takes no arguments."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[ToolInput]
    handler: ToolHandler
    # Any-of semantics. Empty means every authenticated caller may use the tool.
    required_permissions: frozenset[str] = frozenset()
    read_only: bool = False
    destructive: bool = False

    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


def is_visible(spec: ToolSpec, permissions: Iterable[str]) -> bool:
    """True if a caller holding *permissions* should be offered *spec*.

    Advisory only: the REST layer still enforces project scoping, so a visible
    tool can still answer 403 for a particular project.
    """
    perms = set(permissions)
    if not spec.required_permissions or "admin" in perms:
        return True
    return bool(spec.required_permissions & perms)


def visible_tools(tools: Iterable[ToolSpec], permissions: Iterable[str]) -> list[ToolSpec]:
    perms = set(permissions)
    return [spec for spec in tools if is_visible(spec, perms)]
