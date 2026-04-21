"""Roles, user-roles, and invitations

Revision ID: 005
Revises: 004
Create Date: 2026-04-21

Adds RBAC tables (roles, user_roles) and an invitations table so admins can
invite new members with a specific role.  Seeds three system roles: admin,
developer, viewer.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADMIN_ROLE_ID = uuid.UUID("00000000-0000-4000-a000-000000000001")
DEVELOPER_ROLE_ID = uuid.UUID("00000000-0000-4000-a000-000000000002")
VIEWER_ROLE_ID = uuid.UUID("00000000-0000-4000-a000-000000000003")


def upgrade() -> None:
    roles_table = op.create_table(
        "roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False, index=True),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("permissions", ARRAY(sa.String(100)), server_default="{}", nullable=False),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.bulk_insert(roles_table, [
        {
            "id": ADMIN_ROLE_ID,
            "name": "admin",
            "description": "Full access to all resources and settings",
            "permissions": [
                "admin", "projects.manage", "pipelines.manage", "builds.manage",
                "secrets.manage", "agents.manage", "users.manage", "roles.manage",
                "invites.manage", "settings.manage",
            ],
            "is_system": True,
        },
        {
            "id": DEVELOPER_ROLE_ID,
            "name": "developer",
            "description": "Can manage projects, pipelines, and trigger builds",
            "permissions": [
                "projects.read", "projects.manage",
                "pipelines.read", "pipelines.manage",
                "builds.read", "builds.manage",
                "secrets.read", "secrets.manage",
                "agents.read",
            ],
            "is_system": True,
        },
        {
            "id": VIEWER_ROLE_ID,
            "name": "viewer",
            "description": "Read-only access to projects, pipelines, and builds",
            "permissions": [
                "projects.read", "pipelines.read", "builds.read",
                "secrets.read", "agents.read",
            ],
            "is_system": True,
        },
    ])

    op.create_table(
        "user_roles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "role_id", UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("scope_type", sa.String(50), nullable=False, server_default="global"),
        sa.Column("scope_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "role_id", "scope_type", "scope_id", name="uq_user_role_scope"),
    )

    op.create_table(
        "invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column(
            "role_id", UUID(as_uuid=True),
            sa.ForeignKey("roles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("invites")
    op.drop_table("user_roles")
    op.drop_table("roles")
