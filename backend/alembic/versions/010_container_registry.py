"""Container registry tables and registry permissions

Revision ID: 010
Revises: 009
Create Date: 2026-04-23

Adds container_repositories, container_images, container_tags,
registry_deploy_tokens, and registry_events tables for the embedded
OCI/Docker registry (PRD §6.13).  Also adds registry.read, registry.push,
and registry.manage permissions to the seeded system roles.
"""
from typing import Sequence, Union
import uuid

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ADMIN_ROLE_ID = uuid.UUID("00000000-0000-4000-a000-000000000001")
DEVELOPER_ROLE_ID = uuid.UUID("00000000-0000-4000-a000-000000000002")
VIEWER_ROLE_ID = uuid.UUID("00000000-0000-4000-a000-000000000003")


def upgrade() -> None:
    op.create_table(
        "container_repositories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("allow_anonymous_pull", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("immutable_tags", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("quota_bytes", sa.BigInteger(), nullable=True),
        sa.Column("used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("project_id", "name", name="uq_container_repo_project_name"),
    )

    op.create_table(
        "container_images",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id", UUID(as_uuid=True),
            sa.ForeignKey("container_repositories.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("digest", sa.String(255), nullable=False, index=True),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("config_digest", sa.String(255), nullable=True),
        sa.Column(
            "build_id", UUID(as_uuid=True),
            sa.ForeignKey("builds.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "pushed_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("repository_id", "digest", name="uq_container_image_repo_digest"),
    )

    op.create_table(
        "container_tags",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id", UUID(as_uuid=True),
            sa.ForeignKey("container_repositories.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column(
            "image_id", UUID(as_uuid=True),
            sa.ForeignKey("container_images.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("repository_id", "name", name="uq_container_tag_repo_name"),
    )

    op.create_table(
        "registry_deploy_tokens",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id", UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("token_hash", sa.String(255), nullable=False),
        sa.Column("token_hint", sa.String(20), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False, server_default="pull"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    op.create_table(
        "registry_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "repository_id", UUID(as_uuid=True),
            sa.ForeignKey("container_repositories.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("digest", sa.String(255), nullable=True),
        sa.Column("tag", sa.String(255), nullable=True),
        sa.Column(
            "actor_id", UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # Add registry permissions to seeded roles.
    roles = sa.table(
        "roles",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("permissions", ARRAY(sa.String(100))),
    )

    op.execute(
        roles.update()
        .where(roles.c.id == ADMIN_ROLE_ID)
        .values(permissions=sa.func.array_cat(
            roles.c.permissions,
            sa.cast(
                sa.literal_column("'{registry.read,registry.push,registry.manage}'"),
                ARRAY(sa.String(100)),
            ),
        ))
    )

    op.execute(
        roles.update()
        .where(roles.c.id == DEVELOPER_ROLE_ID)
        .values(permissions=sa.func.array_cat(
            roles.c.permissions,
            sa.cast(
                sa.literal_column("'{registry.read,registry.push}'"),
                ARRAY(sa.String(100)),
            ),
        ))
    )

    op.execute(
        roles.update()
        .where(roles.c.id == VIEWER_ROLE_ID)
        .values(permissions=sa.func.array_cat(
            roles.c.permissions,
            sa.cast(
                sa.literal_column("'{registry.read}'"),
                ARRAY(sa.String(100)),
            ),
        ))
    )


def downgrade() -> None:
    op.drop_table("registry_events")
    op.drop_table("registry_deploy_tokens")
    op.drop_table("container_tags")
    op.drop_table("container_images")
    op.drop_table("container_repositories")

    roles = sa.table(
        "roles",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("permissions", ARRAY(sa.String(100))),
    )

    op.execute(
        roles.update()
        .where(roles.c.id == ADMIN_ROLE_ID)
        .values(permissions=sa.func.array_remove(
            sa.func.array_remove(
                sa.func.array_remove(roles.c.permissions, "registry.read"),
                "registry.push",
            ),
            "registry.manage",
        ))
    )
    op.execute(
        roles.update()
        .where(roles.c.id == DEVELOPER_ROLE_ID)
        .values(permissions=sa.func.array_remove(
            sa.func.array_remove(roles.c.permissions, "registry.read"),
            "registry.push",
        ))
    )
    op.execute(
        roles.update()
        .where(roles.c.id == VIEWER_ROLE_ID)
        .values(permissions=sa.func.array_remove(roles.c.permissions, "registry.read"))
    )
