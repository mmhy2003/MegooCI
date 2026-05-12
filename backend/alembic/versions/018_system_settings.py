"""Add system_settings key-value table

Revision ID: 018
Revises: 017
Create Date: 2026-05-12

Stores runtime configuration overrides (e.g. AI provider, base URL, model)
so admins can change them from the UI without restarting the backend.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(255), primary_key=True),
        sa.Column("value", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("system_settings")
