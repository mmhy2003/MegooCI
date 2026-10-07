"""Helpers shared by the tool modules."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import Field

from app.mcp.client import ApiError
from app.mcp.registry import ToolInput

CASCADE_NOTE = (
    " A cascade delete is not available through MCP; a person must do it in "
    "the MegooCI web UI."
)


class PageInput(ToolInput):
    skip: int = Field(0, ge=0, description="Number of rows to skip.")
    limit: int = Field(20, ge=1, le=100, description="Maximum rows to return.")


def pick(row: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    """A reduced copy of *row* holding only *keys*."""
    return {key: row.get(key) for key in keys}


def body_of(args: ToolInput, *, exclude: set[str]) -> dict[str, Any]:
    """The JSON body for a create/update call: only fields with a value.

    Agents often pass ``null`` for optional arguments they do not care about,
    so null always means "leave unchanged" and is never sent to the API.
    """
    return args.model_dump(mode="json", exclude_none=True, exclude=exclude)


def require_changes(body: dict[str, Any]) -> None:
    if not body:
        raise ApiError(400, "Provide at least one field to update.")


def explain_blocked_delete(error: ApiError) -> ApiError:
    """Tell the agent that a 409 on delete cannot be forced through MCP."""
    if error.status_code != 409:
        return error
    detail = error.detail if isinstance(error.detail, str) else str(error.detail)
    return ApiError(409, detail + CASCADE_NOTE)
