from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.deps import get_current_active_user
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


@router.get("", response_model=SearchResponse)
async def search(
    q: str = Query(..., min_length=1, max_length=200, description="Search query"),
    limit: int = Query(5, ge=1, le=20),
    _current_user: User = Depends(get_current_active_user),
) -> SearchResponse:
    grouped = await multi_search(q, limit=limit)

    results: list[SearchHit] = []
    results.extend(_project_hits(grouped.get("projects", [])))
    results.extend(_pipeline_hits(grouped.get("pipelines", [])))
    results.extend(_build_hits(grouped.get("builds", [])))

    return SearchResponse(query=q, results=results)
