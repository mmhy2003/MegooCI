"""Meilisearch integration: index management, document syncing, and search."""

from __future__ import annotations

import logging
from typing import Any

from meilisearch_python_sdk import AsyncClient
from meilisearch_python_sdk.models.settings import MeilisearchSettings
from meilisearch_python_sdk.models.search import SearchParams
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

INDEX_PROJECTS = "projects"
INDEX_PIPELINES = "pipelines"
INDEX_BUILDS = "builds"

INDEX_SETTINGS: dict[str, MeilisearchSettings] = {
    INDEX_PROJECTS: MeilisearchSettings(
        searchable_attributes=["name", "slug", "description"],
        filterable_attributes=["created_by"],
        sortable_attributes=["created_at"],
        displayed_attributes=[
            "id", "name", "slug", "description", "created_at",
        ],
    ),
    INDEX_PIPELINES: MeilisearchSettings(
        searchable_attributes=["name", "source_repo_url", "default_branch"],
        filterable_attributes=["project_id", "enabled"],
        sortable_attributes=["created_at"],
        displayed_attributes=[
            "id", "name", "project_id", "source_repo_url",
            "default_branch", "enabled", "created_at",
        ],
    ),
    INDEX_BUILDS: MeilisearchSettings(
        searchable_attributes=["branch", "commit_sha", "status", "trigger_type"],
        filterable_attributes=["pipeline_id", "status"],
        sortable_attributes=["created_at", "number"],
        displayed_attributes=[
            "id", "pipeline_id", "number", "branch", "commit_sha",
            "status", "trigger_type", "created_at",
        ],
    ),
}


def _get_client() -> AsyncClient:
    settings = get_settings()
    return AsyncClient(
        settings.MEGOOCI_MEILISEARCH_URL,
        settings.MEGOOCI_MEILISEARCH_API_KEY,
    )


async def ensure_indexes() -> None:
    """Create indexes and apply settings. Safe to call on every startup."""
    async with _get_client() as client:
        for uid, idx_settings in INDEX_SETTINGS.items():
            await client.get_or_create_index(uid, primary_key="id")
            index = client.index(uid)
            await index.update_settings(idx_settings)
    logger.info("Meilisearch indexes configured")


# ── Document helpers ────────────────────────────────────────────────────

def _project_doc(project: Any) -> dict[str, Any]:
    return {
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "description": project.description or "",
        "created_by": str(project.created_by),
        "created_at": project.created_at.isoformat() if project.created_at else "",
    }


def _pipeline_doc(pipeline: Any) -> dict[str, Any]:
    return {
        "id": str(pipeline.id),
        "name": pipeline.name,
        "project_id": str(pipeline.project_id),
        "source_repo_url": pipeline.source_repo_url or "",
        "default_branch": pipeline.default_branch or "",
        "enabled": pipeline.enabled,
        "created_at": pipeline.created_at.isoformat() if pipeline.created_at else "",
    }


def _build_doc(build: Any) -> dict[str, Any]:
    return {
        "id": str(build.id),
        "pipeline_id": str(build.pipeline_id),
        "number": build.number,
        "branch": build.branch or "",
        "commit_sha": build.commit_sha or "",
        "status": build.status or "",
        "trigger_type": build.trigger_type or "",
        "created_at": build.created_at.isoformat() if build.created_at else "",
    }


# ── Single-document index operations (fire-and-forget safe) ────────────

async def index_project(project: Any) -> None:
    try:
        async with _get_client() as client:
            index = client.index(INDEX_PROJECTS)
            await index.add_documents([_project_doc(project)])
    except Exception:
        logger.warning("Failed to index project %s", project.id, exc_info=True)


async def index_pipeline(pipeline: Any) -> None:
    try:
        async with _get_client() as client:
            index = client.index(INDEX_PIPELINES)
            await index.add_documents([_pipeline_doc(pipeline)])
    except Exception:
        logger.warning("Failed to index pipeline %s", pipeline.id, exc_info=True)


async def index_build(build: Any) -> None:
    try:
        async with _get_client() as client:
            index = client.index(INDEX_BUILDS)
            await index.add_documents([_build_doc(build)])
    except Exception:
        logger.warning("Failed to index build %s", build.id, exc_info=True)


async def remove_project(project_id: str) -> None:
    try:
        async with _get_client() as client:
            index = client.index(INDEX_PROJECTS)
            await index.delete_document(project_id)
    except Exception:
        logger.warning("Failed to remove project %s from index", project_id, exc_info=True)


async def remove_pipeline(pipeline_id: str) -> None:
    try:
        async with _get_client() as client:
            index = client.index(INDEX_PIPELINES)
            await index.delete_document(pipeline_id)
    except Exception:
        logger.warning("Failed to remove pipeline %s from index", pipeline_id, exc_info=True)


async def remove_build(build_id: str) -> None:
    try:
        async with _get_client() as client:
            index = client.index(INDEX_BUILDS)
            await index.delete_document(build_id)
    except Exception:
        logger.warning("Failed to remove build %s from index", build_id, exc_info=True)


# ── Full re-sync (called once on startup) ──────────────────────────────

async def sync_all(db: AsyncSession) -> None:
    """Bulk-sync all projects, pipelines, and builds into Meilisearch."""
    from app.models.project import Project
    from app.models.pipeline import Pipeline
    from app.models.build import Build

    async with _get_client() as client:
        # Projects
        result = await db.execute(select(Project))
        projects = [_project_doc(p) for p in result.scalars().all()]
        if projects:
            idx = client.index(INDEX_PROJECTS)
            await idx.add_documents(projects)
        logger.info("Synced %d projects to Meilisearch", len(projects))

        # Pipelines
        result = await db.execute(select(Pipeline))
        pipelines = [_pipeline_doc(p) for p in result.scalars().all()]
        if pipelines:
            idx = client.index(INDEX_PIPELINES)
            await idx.add_documents(pipelines)
        logger.info("Synced %d pipelines to Meilisearch", len(pipelines))

        # Builds (limit to most recent 500 to avoid huge payloads)
        result = await db.execute(
            select(Build).order_by(Build.created_at.desc()).limit(500)
        )
        builds = [_build_doc(b) for b in result.scalars().all()]
        if builds:
            idx = client.index(INDEX_BUILDS)
            await idx.add_documents(builds)
        logger.info("Synced %d builds to Meilisearch", len(builds))


# ── Multi-index search ──────────────────────────────────────────────────

async def multi_search(
    query: str,
    *,
    limit: int = 5,
    indexes: list[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Search across all (or selected) indexes and return grouped results."""
    target_indexes = indexes or [INDEX_PROJECTS, INDEX_PIPELINES, INDEX_BUILDS]

    queries = [
        SearchParams(index_uid=uid, query=query, limit=limit)
        for uid in target_indexes
    ]

    async with _get_client() as client:
        raw_results = await client.multi_search(queries)

    grouped: dict[str, list[dict[str, Any]]] = {}
    for result in raw_results:
        grouped[result.index_uid] = [dict(hit) for hit in result.hits]

    return grouped
