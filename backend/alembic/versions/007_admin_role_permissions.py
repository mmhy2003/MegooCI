"""Add git_connections.manage and users.read to admin role

Revision ID: 007
Revises: 006
Create Date: 2026-04-21

The RBAC consolidation replaces ``get_current_admin_user`` with
``require_permission(...)`` on git-connection and user endpoints.
The admin system role needs the new permission strings so existing
admin users keep access.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADMIN_PERMISSIONS = [
    "admin",
    "projects.read", "projects.manage",
    "pipelines.read", "pipelines.manage",
    "builds.read", "builds.manage",
    "secrets.read", "secrets.manage",
    "agents.read", "agents.manage",
    "users.read", "users.manage",
    "roles.manage",
    "invites.manage",
    "settings.manage",
    "git_connections.manage",
]

OLD_ADMIN_PERMISSIONS = [
    "admin",
    "projects.manage", "pipelines.manage", "builds.manage",
    "secrets.manage", "agents.manage", "users.manage",
    "roles.manage", "invites.manage", "settings.manage",
]


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = :perms WHERE name = 'admin'"
        ).bindparams(perms=ADMIN_PERMISSIONS)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = :perms WHERE name = 'admin'"
        ).bindparams(perms=OLD_ADMIN_PERMISSIONS)
    )
