from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_active_user, _collect_permissions
from app.models.user import User
from app.services.search import multi_search

router = APIRouter()


class SearchHit(BaseModel):
    id: str
    type: str
    title: str
    subtitle: str | None = None
    url: str
    extra: dict[str, Any] = {}


class SearchResponse(BaseModel):
    query: str
    results: list[SearchHit]


def _project_hits(hits: list[dict[str, Any]]) -> list[SearchHit]:
    out: list[SearchHit] = []
    for h in hits:
        out.append(SearchHit(
            id=h["id"],
            type="project",
            title=h.get("name", ""),
            subtitle=h.get("description") or h.get("slug", ""),
            url=f"/projects/{h['id']}",
        ))
    return out


def _pipeline_hits(hits: list[dict[str, Any]]) -> list[SearchHit]:
    out: list[SearchHit] = []
    for h in hits:
        out.append(SearchHit(
            id=h["id"],
            type="pipeline",
            title=h.get("name", ""),
            subtitle=h.get("source_repo_url") or h.get("default_branch", ""),
            url=f"/pipelines/{h['id']}",
        ))
    return out


def _build_hits(hits: list[dict[str, Any]]) -> list[SearchHit]:
    out: list[SearchHit] = []
    for h in hits:
        number = h.get("number", "?")
        status = h.get("status", "")
        out.append(SearchHit(
            id=h["id"],
            type="build",
            title=f"Build #{number}",
            subtitle=f"{status} — {h.get('branch', '')}",
            url=f"/builds/{h['id']}",
            extra={"status": status},
        ))
    return out


def _artifact_hits(hits: list[dict[str, Any]]) -> list[SearchHit]:
    out: list[SearchHit] = []
    for h in hits:
        path = h.get("relative_path", "")
        # Clicking an artifact result jumps to /artifacts with the filename
        # pre-filled in the page's search box so the user lands on a filtered view.
        from urllib.parse import quote
        url = f"/artifacts?q={quote(path)}"
        subtitle_parts = []
        if h.get("project_name"):
            subtitle_parts.append(h["project_name"])
        if h.get("pipeline_name"):
            subtitle_parts.append(h["pipeline_name"])
        if h.get("build_number") is not None:
            subtitle_parts.append(f"build #{h['build_number']}")
        out.append(SearchHit(
            id=h["id"],
            type="artifact",
            title=path or "(unnamed artifact)",
            subtitle=" · ".join(subtitle_parts) or None,
            url=url,
        ))
    return out


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_active_user),
) -> SearchResponse:
    perms = _collect_permissions(current_user)

    allowed_indexes: list[str] = []
    if "admin" in perms or "projects.read" in perms:
        allowed_indexes.append("projects")
    if "admin" in perms or "pipelines.read" in perms:
        allowed_indexes.append("pipelines")
    if "admin" in perms or "builds.read" in perms:
        allowed_indexes.append("builds")
    if "admin" in perms or "artifacts.read" in perms:
        allowed_indexes.append("artifacts")

    if not allowed_indexes:
        return SearchResponse(query=q, results=[])

    grouped = await multi_search(q, limit=limit, indexes=allowed_indexes)

    results: list[SearchHit] = []
    results.extend(_project_hits(grouped.get("projects", [])))
    results.extend(_pipeline_hits(grouped.get("pipelines", [])))
    results.extend(_build_hits(grouped.get("builds", [])))
    results.extend(_artifact_hits(grouped.get("artifacts", [])))

    return SearchResponse(query=q, results=results)
