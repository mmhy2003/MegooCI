"""Reusable transform for migration 023: convert global developer/viewer role
assignments into per-project assignments across all existing projects."""
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def convert_global_nonadmin_to_scoped(db: AsyncSession) -> int:
    from app.models.project import Project
    from app.models.role import Role, UserRole

    project_ids = [p for (p,) in (await db.execute(select(Project.id))).all()]
    nonadmin_role_ids = {
        rid for (rid,) in (
            await db.execute(select(Role.id).where(Role.name.in_(("developer", "viewer"))))
        ).all()
    }
    global_rows = (await db.execute(
        select(UserRole).where(
            UserRole.scope_type == "global",
            UserRole.role_id.in_(nonadmin_role_ids),
        )
    )).scalars().all()

    converted = 0
    for ur in global_rows:
        for pid in project_ids:
            db.add(UserRole(id=uuid.uuid4(), user_id=ur.user_id, role_id=ur.role_id,
                            scope_type="project", scope_id=pid))
        await db.delete(ur)
        converted += 1
    return converted
