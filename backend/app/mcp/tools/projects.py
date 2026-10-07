"""Project tools."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field

from app.mcp.client import ApiClient, ApiError
from app.mcp.registry import ToolInput, ToolSpec
from app.mcp.tools._common import (
    PageInput,
    body_of,
    explain_blocked_delete,
    pick,
    require_changes,
)

_ROW_KEYS = ("id", "name", "slug", "description", "parent_id")
_REPO_KEYS = ("id", "repo_url", "default_branch", "display_name")
_READ = frozenset({"projects.read"})
_MANAGE = frozenset({"projects.manage"})


class ProjectIdInput(ToolInput):
    project_id: uuid.UUID = Field(description="Project ID (UUID) from list_projects or search.")


class CreateProjectInput(ToolInput):
    name: str = Field(min_length=1, description="Project name.")
    description: str | None = Field(None, description="Optional description.")
    parent_id: uuid.UUID | None = Field(None, description="Optional parent project ID.")


class UpdateProjectInput(ToolInput):
    project_id: uuid.UUID = Field(description="Project ID (UUID).")
    name: str | None = Field(None, description="New name.")
    description: str | None = Field(None, description="New description.")


async def _list_projects(api: ApiClient, args: PageInput) -> dict[str, Any]:
    body = await api.get("/projects", params={"skip": args.skip, "limit": args.limit})
    return {"total": body["total"], "items": [pick(p, _ROW_KEYS) for p in body["items"]]}


async def _get_project(api: ApiClient, args: ProjectIdInput) -> dict[str, Any]:
    return await api.get(f"/projects/{args.project_id}")


async def _list_project_repositories(
    api: ApiClient, args: ProjectIdInput
) -> list[dict[str, Any]]:
    rows = await api.get(f"/projects/{args.project_id}/repositories/")
    return [pick(r, _REPO_KEYS) for r in rows]


async def _create_project(api: ApiClient, args: CreateProjectInput) -> dict[str, Any]:
    return await api.post("/projects", json_body=body_of(args, exclude=set()))


async def _update_project(api: ApiClient, args: UpdateProjectInput) -> dict[str, Any]:
    body = body_of(args, exclude={"project_id"})
    require_changes(body)
    return await api.put(f"/projects/{args.project_id}", json_body=body)


async def _delete_project(api: ApiClient, args: ProjectIdInput) -> dict[str, Any]:
    try:
        await api.delete(f"/projects/{args.project_id}")
    except ApiError as error:
        raise explain_blocked_delete(error) from error
    return {"deleted": True, "project_id": str(args.project_id)}


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_projects",
        description="List the projects this token can see, newest first.",
        input_model=PageInput,
        handler=_list_projects,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="get_project",
        description="Get one project by ID.",
        input_model=ProjectIdInput,
        handler=_get_project,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="list_project_repositories",
        description=(
            "List the Git repositories linked to a project. Use a repository's "
            "`id` as `project_repository_id` when creating a pipeline."
        ),
        input_model=ProjectIdInput,
        handler=_list_project_repositories,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="create_project",
        description="Create a project.",
        input_model=CreateProjectInput,
        handler=_create_project,
        required_permissions=_MANAGE,
    ),
    ToolSpec(
        name="update_project",
        description="Rename a project or change its description.",
        input_model=UpdateProjectInput,
        handler=_update_project,
        required_permissions=_MANAGE,
    ),
    ToolSpec(
        name="delete_project",
        description=(
            "Delete an empty project. Refuses when the project still has "
            "pipelines, repositories, secrets or child projects; removing those "
            "in bulk must be done by a person in the web UI."
        ),
        input_model=ProjectIdInput,
        handler=_delete_project,
        required_permissions=_MANAGE,
        destructive=True,
    ),
)
