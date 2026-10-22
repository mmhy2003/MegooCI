"""Turning a user's administrator access on or off.

An administrator holds both the ``is_admin`` flag and a global row for the
role named ``admin``. The server treats either one as full admin, so the two
must never be changed apart: every grant and every removal goes through
:func:`set_administrator`.
"""

from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import record as audit_record
from app.models.role import Role, UserRole
from app.models.user import User

ADMIN_ROLE_NAME = "admin"


class AdministratorChangeError(Exception):
    """The change would lock someone out; ``str()`` is the message to show."""


def _admin_role_ids():
    return select(Role.id).where(Role.name == ADMIN_ROLE_NAME)


def _global_admin_rows(user_id: uuid.UUID):
    return select(UserRole).where(
        UserRole.user_id == user_id,
        UserRole.scope_type == "global",
        UserRole.role_id.in_(_admin_role_ids()),
    )


async def is_admin_role_row(db: AsyncSession, user_role: UserRole) -> bool:
    """Whether *user_role* is a global row for the role named ``admin``."""
    if user_role.scope_type != "global":
        return False
    role = await db.get(Role, user_role.role_id)
    return role is not None and role.name == ADMIN_ROLE_NAME


async def _other_active_administrators(db: AsyncSession, user_id: uuid.UUID) -> int:
    holds_role = select(UserRole.user_id).where(
        UserRole.scope_type == "global", UserRole.role_id.in_(_admin_role_ids())
    )
    result = await db.execute(
        select(User.id).where(
            User.id != user_id,
            User.is_active.is_(True),
            or_(User.is_admin.is_(True), User.id.in_(holds_role)),
        )
    )
    return len(result.all())


async def set_administrator(
    db: AsyncSession, user: User, on: bool, *, actor_id: uuid.UUID | None
) -> None:
    """Give or take away administrator access: the flag and the global admin
    role together. Other roles, project rows included, are left alone.
    Flushes; the caller commits."""
    rows = list((await db.execute(_global_admin_rows(user.id))).scalars())
    had_flag, had_role = bool(user.is_admin), bool(rows)

    if not on:
        if actor_id is not None and actor_id == user.id:
            raise AdministratorChangeError("You cannot remove your own administrator access")
        if await _other_active_administrators(db, user.id) == 0:
            raise AdministratorChangeError("There must be at least one active administrator")
        for row in rows:
            await db.delete(row)
    elif not rows:
        role_id = (await db.execute(_admin_role_ids())).scalars().first()
        # Without a role named admin (never the case after migration 005)
        # the flag alone still makes them an administrator.
        if role_id is not None:
            db.add(UserRole(user_id=user.id, role_id=role_id, scope_type="global"))

    user.is_admin = on
    await audit_record(
        action="administrator_granted" if on else "administrator_revoked",
        actor_id=actor_id,
        target_type="user",
        target_id=user.id,
        metadata={"had_flag": had_flag, "had_role": had_role},
        db=db,
    )
    await db.flush()
