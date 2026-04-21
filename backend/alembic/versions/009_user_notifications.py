"""In-app user notifications.

Revision ID: 009
Revises: 008
Create Date: 2026-04-21

Adds the user_notifications table for per-user in-app notifications with
indexes for fast unread-count queries and timeline listing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_notifications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(50), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("entity_type", sa.String(50), nullable=True),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_user_notifications_user_created",
        "user_notifications",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_user_notifications_user_unread",
        "user_notifications",
        ["user_id", "read_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_notifications_user_unread",
        table_name="user_notifications",
    )
    op.drop_index(
        "ix_user_notifications_user_created",
        table_name="user_notifications",
    )
    op.drop_table("user_notifications")
