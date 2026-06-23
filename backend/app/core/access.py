"""Project-scoped access: which projects a user can act on for a permission.

The single source of truth for visibility filtering. Composes with the
permission helpers in app.core.deps (which already apply any active PAT scope).
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import effective_scoped_permissions
from app.models.user import User


class _AllProjects:
    """Sentinel meaning 'every project' (admin or a global permission grant)."""
    _singleton = None

    def __new__(cls):
        if cls._singleton is None:
            cls._singleton = super().__new__(cls)
        return cls._singleton

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "ALL_PROJECTS"


ALL_PROJECTS = _AllProjects()


def has_global_permission(user: User, permission: str) -> bool:
    """True if *user* holds *permission* GLOBALLY — via admin or a global-scope
    role — NOT via a project-scoped role.

    Use this for 'global-only' gates. Do NOT use deps.effective_permissions for
    that purpose: it unions ALL roles regardless of scope, so a project-scoped
    role would leak as a global grant.
    """
    global_perms = effective_scoped_permissions(user, "global", None)
    return "admin" in global_perms or permission in global_perms


def accessible_project_ids(user: User, permission: str):
    """Projects in which *user* effectively holds *permission*.

    Returns ALL_PROJECTS when the user is admin or holds *permission* globally;
    otherwise the set of project_ids granted via project-scoped roles. Empty set
    when the user has no qualifying assignment.
    """
    if has_global_permission(user, permission):
        return ALL_PROJECTS

    pids: set[uuid.UUID] = set()
    for ur in user.user_roles:
        if ur.scope_type == "project" and ur.scope_id is not None:
            scoped = effective_scoped_permissions(user, "project", ur.scope_id)
            if permission in scoped:
                pids.add(ur.scope_id)
    return pids


async def project_id_for_pipeline(db: AsyncSession, pipeline_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.pipeline import Pipeline
    return await db.scalar(select(Pipeline.project_id).where(Pipeline.id == pipeline_id))


async def project_id_for_build(db: AsyncSession, build_id: uuid.UUID) -> uuid.UUID | None:
    from app.models.build import Build
    from app.models.pipeline import Pipeline
    return await db.scalar(
        select(Pipeline.project_id)
        .join(Build, Build.pipeline_id == Pipeline.id)
        .where(Build.id == build_id)
    )
