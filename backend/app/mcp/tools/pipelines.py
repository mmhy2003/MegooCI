"""Pipeline tools."""

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

_ROW_KEYS = ("id", "project_id", "name", "default_branch", "enabled")
_READ = frozenset({"pipelines.read"})
_MANAGE = frozenset({"pipelines.manage"})


class ListPipelinesInput(PageInput):
    project_id: uuid.UUID | None = Field(
        None, description="Only pipelines in this project. Omit for all visible projects."
    )


class PipelineIdInput(ToolInput):
    pipeline_id: uuid.UUID = Field(
        description="Pipeline ID (UUID) from list_pipelines or search."
    )


class ValidateYamlInput(ToolInput):
    yaml_content: str = Field(description="The pipeline YAML to check.")


class CreatePipelineInput(ToolInput):
    project_id: uuid.UUID = Field(description="Project that will own the pipeline.")
    name: str = Field(min_length=1, description="Pipeline name.")
    yaml_content: str | None = Field(None, description="Pipeline definition (YAML).")
    default_branch: str | None = Field(None, description="Branch built by default.")
    project_repository_id: uuid.UUID | None = Field(
        None, description="Linked repository ID from list_project_repositories."
    )
    source_repo_url: str | None = Field(
        None, description="Repository URL, when not using a linked repository."
    )


class UpdatePipelineInput(ToolInput):
    pipeline_id: uuid.UUID = Field(description="Pipeline ID (UUID).")
    # min_length: an empty string must not blank a column (see body_of).
    name: str | None = Field(None, min_length=1, description="New name.")
    yaml_content: str | None = Field(
        None, min_length=1, description="New pipeline definition (YAML)."
    )
    default_branch: str | None = Field(None, min_length=1, description="New default branch.")
    enabled: bool | None = Field(None, description="False disables the pipeline.")
    project_repository_id: uuid.UUID | None = Field(
        None, description="Linked repository ID."
    )
    source_repo_url: str | None = Field(None, min_length=1, description="Repository URL.")


async def _list_pipelines(api: ApiClient, args: ListPipelinesInput) -> dict[str, Any]:
    body = await api.get(
        "/pipelines",
        params={
            "project_id": str(args.project_id) if args.project_id else None,
            "skip": args.skip,
            "limit": args.limit,
        },
    )
    return {"total": body["total"], "items": [pick(p, _ROW_KEYS) for p in body["items"]]}


async def _get_pipeline(api: ApiClient, args: PipelineIdInput) -> dict[str, Any]:
    return await api.get(f"/pipelines/{args.pipeline_id}")


async def _validate_pipeline_yaml(api: ApiClient, args: ValidateYamlInput) -> dict[str, Any]:
    return await api.post("/pipelines/validate", json_body={"yaml_content": args.yaml_content})


async def _create_pipeline(api: ApiClient, args: CreatePipelineInput) -> dict[str, Any]:
    return await api.post("/pipelines", json_body=body_of(args, exclude=set()))


async def _update_pipeline(api: ApiClient, args: UpdatePipelineInput) -> dict[str, Any]:
    body = body_of(args, exclude={"pipeline_id"})
    require_changes(body)
    return await api.put(f"/pipelines/{args.pipeline_id}", json_body=body)


async def _delete_pipeline(api: ApiClient, args: PipelineIdInput) -> dict[str, Any]:
    try:
        await api.delete(f"/pipelines/{args.pipeline_id}")
    except ApiError as error:
        raise explain_blocked_delete(error) from error
    return {"deleted": True, "pipeline_id": str(args.pipeline_id)}


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_pipelines",
        description=(
            "List pipelines, newest first, without their YAML. Use get_pipeline "
            "to read a pipeline's definition."
        ),
        input_model=ListPipelinesInput,
        handler=_list_pipelines,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="get_pipeline",
        description="Get one pipeline by ID, including its YAML definition.",
        input_model=PipelineIdInput,
        handler=_get_pipeline,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="validate_pipeline_yaml",
        description=(
            "Check pipeline YAML without saving it. Returns `valid` and a list "
            "of errors with line and column. Run this before create_pipeline or "
            "update_pipeline."
        ),
        input_model=ValidateYamlInput,
        handler=_validate_pipeline_yaml,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="create_pipeline",
        description="Create a pipeline in a project.",
        input_model=CreatePipelineInput,
        handler=_create_pipeline,
        required_permissions=_MANAGE,
    ),
    ToolSpec(
        name="update_pipeline",
        description=(
            "Change a pipeline's name, YAML, default branch or repository, or "
            "enable/disable it. Only the fields you pass are changed."
        ),
        input_model=UpdatePipelineInput,
        handler=_update_pipeline,
        required_permissions=_MANAGE,
    ),
    ToolSpec(
        name="delete_pipeline",
        description=(
            "Delete a pipeline that has no builds, triggers or webhook "
            "endpoints. Refuses otherwise; deleting a pipeline together with "
            "its history must be done by a person in the web UI."
        ),
        input_model=PipelineIdInput,
        handler=_delete_pipeline,
        required_permissions=_MANAGE,
        destructive=True,
    ),
)
