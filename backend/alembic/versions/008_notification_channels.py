"""Notification channels and delivery log.

Revision ID: 008
Revises: 007
Create Date: 2026-04-21

Adds two tables: notification_channels (admin-configured email / slack /
telegram channels) and notification_deliveries (audit log of sent messages).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notification_channels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("channel_type", sa.String(20), nullable=False),
        sa.Column("config_encrypted", sa.LargeBinary, nullable=False),
        sa.Column(
            "enabled", sa.Boolean, nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "validation_status",
            sa.String(10),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "notification_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "channel_id",
            UUID(as_uuid=True),
            sa.ForeignKey("notification_channels.id"),
            nullable=False,
        ),
        sa.Column(
            "build_id",
            UUID(as_uuid=True),
            sa.ForeignKey("builds.id"),
            nullable=True,
        ),
        sa.Column("step_id", UUID(as_uuid=True), nullable=True),
        sa.Column("recipient", sa.String(512), nullable=True),
        sa.Column("subject", sa.String(512), nullable=True),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_notification_deliveries_channel_created",
        "notification_deliveries",
        ["channel_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_notification_deliveries_channel_created",
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
    op.drop_table("notification_channels")
