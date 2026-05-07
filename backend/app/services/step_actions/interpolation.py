"""
Template interpolation for pipeline values.

Replaces ``${{ secrets.NAME }}``, ``${{ env.NAME }}``, ``${{ build.NAME }}``,
``${{ pipeline.NAME }}``, and ``${{ project.NAME }}`` placeholders in
strings and recursively in dicts/lists.  Used by the build executor before
handing config to a step handler.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_secret
from app.models.secret import EnvVar, Secret

_PLACEHOLDER_RE = re.compile(
    r"\$\{\{\s*(secrets|env|build|pipeline|project|megooci)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}"
)


async def load_secrets_for_scope(
    db: AsyncSession,
    project_id: uuid.UUID,
    pipeline_id: uuid.UUID | None = None,
) -> dict[str, str]:
    """Load and decrypt all secrets visible to a pipeline.

    Scoping order (later wins on name collision):
    1. global secrets   (scope_type='global')
    2. project secrets  (scope_type='project', scope_id=project_id)
    3. pipeline secrets (scope_type='pipeline', scope_id=pipeline_id)
    """
    settings = get_settings()
    result: dict[str, str] = {}

    scopes: list[tuple[str, uuid.UUID | None]] = [
        ("global", None),
        ("project", project_id),
    ]
    if pipeline_id:
        scopes.append(("pipeline", pipeline_id))

    for scope_type, scope_id in scopes:
        query = select(Secret).where(Secret.scope_type == scope_type)
        if scope_id is not None:
            query = query.where(Secret.scope_id == scope_id)
        else:
            query = query.where(Secret.scope_id.is_(None))

        rows = await db.execute(query)
        for secret in rows.scalars().all():
            try:
                result[secret.name] = decrypt_secret(
                    secret.encrypted_payload, settings.MEGOOCI_SECRET_KEY
                )
            except Exception:
                result[secret.name] = ""

    return result


async def load_env_vars_for_scope(
    db: AsyncSession,
    project_id: uuid.UUID,
    pipeline_id: uuid.UUID | None = None,
) -> dict[str, str]:
    """Load all env vars visible to a pipeline (same scoping as secrets)."""
    result: dict[str, str] = {}

    scopes: list[tuple[str, uuid.UUID | None]] = [
        ("global", None),
        ("project", project_id),
    ]
    if pipeline_id:
        scopes.append(("pipeline", pipeline_id))

    for scope_type, scope_id in scopes:
        query = select(EnvVar).where(EnvVar.scope_type == scope_type)
        if scope_id is not None:
            query = query.where(EnvVar.scope_id == scope_id)
        else:
            query = query.where(EnvVar.scope_id.is_(None))

        rows = await db.execute(query)
        for ev in rows.scalars().all():
            result[ev.name] = ev.value

    return result


def interpolate_value(
    value: Any,
    secrets: dict[str, str],
    env: dict[str, str],
    builtins: dict[str, dict[str, str]] | None = None,
) -> Any:
    """Recursively replace ``${{ secrets.X }}``, ``${{ env.X }}``,
    ``${{ build.X }}``, ``${{ pipeline.X }}``, and ``${{ project.X }}``
    in a value.

    *builtins* is an optional mapping of namespace → {key: value} for the
    ``build``, ``pipeline``, and ``project`` namespaces.  Example::

        {"build": {"branch": "main", "number": "42"}, ...}

    - Strings are interpolated directly.
    - Dicts/lists are traversed recursively.
    - Other types pass through unchanged.
    """
    if isinstance(value, str):
        return _interpolate_string(value, secrets, env, builtins)
    if isinstance(value, dict):
        return {k: interpolate_value(v, secrets, env, builtins) for k, v in value.items()}
    if isinstance(value, list):
        return [interpolate_value(v, secrets, env, builtins) for v in value]
    return value


def _interpolate_string(
    text: str,
    secrets: dict[str, str],
    env: dict[str, str],
    builtins: dict[str, dict[str, str]] | None = None,
) -> str:
    def _replace(match: re.Match) -> str:
        namespace = match.group(1)
        key = match.group(2)
        if namespace == "secrets":
            return secrets.get(key, "")
        if namespace == "env":
            return env.get(key, "")
        if builtins and namespace in builtins:
            return builtins[namespace].get(key, "")
        return match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, text)


def mask_secrets_in_log(line: str, secrets: dict[str, str]) -> str:
    """Replace any secret value appearing in a log line with ``***``."""
    for value in secrets.values():
        if value and value in line:
            line = line.replace(value, "***")
    return line

