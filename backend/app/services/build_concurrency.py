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
