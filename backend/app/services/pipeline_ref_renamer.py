"""
Pipeline reference renamer.

Scans all pipeline YAML content for ``${{ secrets.OLD }}`` or ``${{ env.OLD }}``
placeholders and replaces them with the new name when a secret or env-var is
renamed.  Updates are committed in a single transaction.

The rename is scoped: global renames hit ALL pipelines; project-scoped renames
only target pipelines belonging to that project.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pipeline import Pipeline


def _build_pattern(namespace: str, old_name: str) -> re.Pattern[str]:
    r"""Build a regex matching ``${{ <namespace>.<old_name> }}``."""
    return re.compile(
        r"\$\{\{\s*"
        + re.escape(namespace)
        + r"\."
        + re.escape(old_name)
        + r"\s*\}\}"
    )


def _build_replacement(namespace: str, new_name: str) -> str:
    """Build the replacement string ``${{ <namespace>.<new_name> }}``."""
    return f"${{{{ {namespace}.{new_name} }}}}"


async def rename_pipeline_references(
    db: AsyncSession,
    *,
    namespace: Literal["secrets", "env"],
    old_name: str,
    new_name: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
) -> list[dict]:
    """Find pipelines referencing ``old_name`` and replace with ``new_name``.

    Returns a list of dicts describing what was updated::

        [{"pipeline_id": "...", "pipeline_name": "...", "occurrences": 3}, ...]
    """
    if old_name == new_name:
        return []

    # Build the query to find relevant pipelines.
    query = select(Pipeline).where(Pipeline.yaml_content.is_not(None))

    if scope_type == "project" and scope_id is not None:
        # Only pipelines in the same project.
        query = query.where(Pipeline.project_id == scope_id)
    # For global scope, search ALL pipelines.

    result = await db.execute(query)
    pipelines = list(result.scalars().all())

    pattern = _build_pattern(namespace, old_name)
    replacement = _build_replacement(namespace, new_name)

    updated: list[dict] = []

    for pipeline in pipelines:
        if not pipeline.yaml_content:
            continue

        count = len(pattern.findall(pipeline.yaml_content))
        if count == 0:
            continue

        new_yaml = pattern.sub(replacement, pipeline.yaml_content)
        pipeline.yaml_content = new_yaml
        updated.append(
            {
                "pipeline_id": str(pipeline.id),
                "pipeline_name": pipeline.name,
                "occurrences": count,
            }
        )

    return updated
