"""Update seeded role permissions for RBAC enforcement

Revision ID: 006
Revises: 005
Create Date: 2026-04-21

Aligns the developer and viewer role permission arrays with the
require_permission() guards now enforced on all API endpoints.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEVELOPER_PERMISSIONS = [
    "projects.read", "projects.manage",
    "pipelines.read", "pipelines.manage",
    "builds.read", "builds.manage",
    "secrets.read", "secrets.manage",
    "agents.read",
]

VIEWER_PERMISSIONS = [
    "projects.read", "pipelines.read", "builds.read",
    "secrets.read", "agents.read",
]

OLD_DEVELOPER_PERMISSIONS = [
    "projects.manage", "pipelines.manage", "builds.manage",
    "secrets.read", "agents.read",
]

OLD_VIEWER_PERMISSIONS = [
    "projects.read", "pipelines.read", "builds.read",
    "agents.read",
]


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = :perms WHERE name = 'developer'"
        ).bindparams(perms=DEVELOPER_PERMISSIONS)
    )
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = :perms WHERE name = 'viewer'"
        ).bindparams(perms=VIEWER_PERMISSIONS)
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = :perms WHERE name = 'developer'"
        ).bindparams(perms=OLD_DEVELOPER_PERMISSIONS)
    )
    op.execute(
        sa.text(
            "UPDATE roles SET permissions = :perms WHERE name = 'viewer'"
        ).bindparams(perms=OLD_VIEWER_PERMISSIONS)
    )
