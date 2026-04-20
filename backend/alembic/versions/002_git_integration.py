"""Git provider integration (PRD §6.16)

Revision ID: 002
Revises: 001
Create Date: 2026-04-20

Adds three tables (git_provider_connections, project_repositories,
webhook_deliveries) plus a nullable project_repository_id FK on pipelines.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON, UUID

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "git_provider_connections",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("provider_type", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(2048), nullable=True),
        sa.Column(
            "auth_mode",
            sa.String(10),
            nullable=False,
            server_default="pat",
        ),
        sa.Column("encrypted_credential", sa.LargeBinary, nullable=False),
        sa.Column("credential_hint", sa.String(16), nullable=True),
        sa.Column("encrypted_refresh_token", sa.LargeBinary, nullable=True),
        sa.Column("oauth_client_id", sa.String(255), nullable=True),
        sa.Column(
            "encrypted_oauth_client_secret", sa.LargeBinary, nullable=True
        ),
        sa.Column("token_scopes", JSON, nullable=True),
        sa.Column(
            "token_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "validation_status",
            sa.String(10),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column(
            "last_validated_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("validation_error", sa.Text, nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )

    op.create_table(
        "project_repositories",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.id"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            UUID(as_uuid=True),
            sa.ForeignKey("git_provider_connections.id"),
            nullable=False,
        ),
        sa.Column("repo_url", sa.String(2048), nullable=False),
        sa.Column(
            "default_branch",
            sa.String(255),
            nullable=False,
            server_default="main",
        ),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column(
            "webhook_slug",
            sa.String(64),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("webhook_secret_hash", sa.String(255), nullable=False),
        sa.Column(
            "last_event_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_event_status", sa.String(20), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=True
        ),
    )

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_repository_id",
            UUID(as_uuid=True),
            sa.ForeignKey("project_repositories.id"),
            nullable=False,
        ),
        sa.Column("provider_delivery_id", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=True),
        sa.Column("branch", sa.String(255), nullable=True),
        sa.Column("commit_sha", sa.String(64), nullable=True),
        sa.Column("author", sa.String(255), nullable=True),
        sa.Column(
            "signature_valid",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "http_status",
            sa.Integer,
            nullable=False,
            server_default="200",
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("payload_excerpt", sa.Text, nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "processed_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.UniqueConstraint(
            "project_repository_id",
            "provider_delivery_id",
            name="uq_webhook_delivery_repo_delivery",
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_repo_received",
        "webhook_deliveries",
        ["project_repository_id", "received_at"],
    )

    op.add_column(
        "pipelines",
        sa.Column(
            "project_repository_id",
            UUID(as_uuid=True),
            sa.ForeignKey("project_repositories.id"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("pipelines", "project_repository_id")
    op.drop_index(
        "ix_webhook_deliveries_repo_received", table_name="webhook_deliveries"
    )
    op.drop_table("webhook_deliveries")
    op.drop_table("project_repositories")
    op.drop_table("git_provider_connections")
