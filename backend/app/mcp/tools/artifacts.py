"""Artifact tools."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field

from app.mcp.client import ApiClient
from app.mcp.registry import ToolInput, ToolSpec
from app.mcp.tools._common import pick

_ROW_KEYS = ("id", "relative_path", "size_bytes", "checksum_sha256", "created_at")
_READ = frozenset({"artifacts.read"})


class BuildArtifactsInput(ToolInput):
    build_id: uuid.UUID = Field(description="Build ID (UUID).")


class ArtifactUrlInput(ToolInput):
    artifact_id: uuid.UUID = Field(description="Artifact ID (UUID) from list_build_artifacts.")
    ttl: int = Field(300, ge=30, le=3600, description="Seconds the URL stays valid.")


async def _list_build_artifacts(
    api: ApiClient, args: BuildArtifactsInput
) -> list[dict[str, Any]]:
    rows = await api.get(f"/builds/{args.build_id}/artifacts")
    return [pick(a, _ROW_KEYS) for a in rows]


async def _get_artifact_download_url(
    api: ApiClient, args: ArtifactUrlInput
) -> dict[str, Any]:
    return await api.get(
        f"/artifacts/{args.artifact_id}/signed-url", params={"ttl": args.ttl}
    )


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="list_build_artifacts",
        description="List the files a build produced.",
        input_model=BuildArtifactsInput,
        handler=_list_build_artifacts,
        required_permissions=_READ,
        read_only=True,
    ),
    ToolSpec(
        name="get_artifact_download_url",
        description=(
            "Get a temporary download URL for an artifact. Fetch the URL "
            "yourself (for example with curl); it needs no extra authentication "
            "and expires after `ttl` seconds."
        ),
        input_model=ArtifactUrlInput,
        handler=_get_artifact_download_url,
        required_permissions=_READ,
        read_only=True,
    ),
)
