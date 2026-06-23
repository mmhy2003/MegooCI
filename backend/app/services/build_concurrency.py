"""Single-run pipeline concurrency: a pipeline never runs two builds at once.

Invariants (enforced by partial unique indexes from migration 021):
  - at most one ``running`` build per pipeline  → serialize
  - at most one ``pending`` build per pipeline  → coalesce (latest wins)

This module holds the helpers the trigger paths and the build executor use to
respect those invariants.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.build import Build


async def pipeline_has_running_build(
    db: AsyncSession,
    pipeline_id: uuid.UUID,
    exclude_build_id: uuid.UUID | None = None,
) -> bool:
    """True if the pipeline currently has a ``running`` build (other than
    ``exclude_build_id``)."""
    stmt = select(Build.id).where(
        Build.pipeline_id == pipeline_id,
        Build.status == "running",
    )
    if exclude_build_id is not None:
        stmt = stmt.where(Build.id != exclude_build_id)
    return (await db.scalar(stmt.limit(1))) is not None


def dispatchable_pending_builds_stmt(limit: int = 20):
    """Select pending builds whose pipeline has NO running build, oldest first.
    Used by the dispatcher so it never starts a build for a busy pipeline."""
    running_sibling = aliased(Build)
    return (
        select(Build)
        .where(
            Build.status == "pending",
            ~select(running_sibling.id)
            .where(
                running_sibling.pipeline_id == Build.pipeline_id,
                running_sibling.status == "running",
            )
            .exists(),
        )
        .order_by(Build.created_at.asc())
        .limit(limit)
    )


async def try_start_build(db: AsyncSession, build: Build) -> bool:
    """Atomically flip ``build`` from pending to running, respecting the
    one-running-per-pipeline index. Returns True if it became running, False if
    the pipeline already has a running build (build is left ``pending``)."""
    build.status = "running"
    build.started_at = datetime.now(timezone.utc)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return False
    return True


async def _find_pending(db: AsyncSession, pipeline_id: uuid.UUID) -> Build | None:
    return await db.scalar(
        select(Build)
        .where(Build.pipeline_id == pipeline_id, Build.status == "pending")
        .limit(1)
    )


async def _coalesce(
    db: AsyncSession, existing: Build, *, default_branch, branch, commit_sha,
    params, triggered_by, trigger_type,
) -> tuple[Build, bool]:
    existing.branch = branch or default_branch
    existing.commit_sha = commit_sha
    existing.params_json = params
    existing.trigger_type = trigger_type
    existing.triggered_by = triggered_by
    await db.commit()
    return existing, False


async def create_or_coalesce_build(
    db: AsyncSession,
    *,
    pipeline_id: uuid.UUID,
    default_branch: str,
    branch: str | None,
    commit_sha: str | None,
    params: dict | None,
    triggered_by: uuid.UUID | None,
    trigger_type: str,
) -> tuple[Build, bool]:
    """Create a pending build for the pipeline, or coalesce into the existing
    pending one (latest wins). Returns ``(build, created)``.

    Contract: ``created=True`` → the build is flushed but NOT committed; the
    caller compiles stages/steps and commits. ``created=False`` → already
    committed; the caller does nothing further (no compile, no enqueue).
    """
    existing = await _find_pending(db, pipeline_id)
    if existing is not None:
        return await _coalesce(
            db, existing, default_branch=default_branch, branch=branch,
            commit_sha=commit_sha, params=params, triggered_by=triggered_by,
            trigger_type=trigger_type,
        )

    max_number = await db.scalar(
        select(func.coalesce(func.max(Build.number), 0)).where(
            Build.pipeline_id == pipeline_id
        )
    )
    build = Build(
        pipeline_id=pipeline_id,
        number=(max_number or 0) + 1,
        branch=branch or default_branch,
        commit_sha=commit_sha,
        status="pending",
        triggered_by=triggered_by,
        trigger_type=trigger_type,
        params_json=params,
    )
    db.add(build)
    try:
        await db.flush()  # fires uq_one_pending_build_per_pipeline on a race
    except IntegrityError:
        await db.rollback()
        existing = await _find_pending(db, pipeline_id)
        if existing is None:  # pragma: no cover - pending vanished post-rollback
            raise
        return await _coalesce(
            db, existing, default_branch=default_branch, branch=branch,
            commit_sha=commit_sha, params=params, triggered_by=triggered_by,
            trigger_type=trigger_type,
        )
    return build, True
