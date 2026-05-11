"""Add current_build_id to agents for busy/idle tracking

Revision ID: 017
Revises: 016
Create Date: 2026-05-11

Agents should only handle one build at a time. This column tracks which
build (if any) an agent is currently executing so the dispatcher can skip
busy agents and queue pending builds instead of dispatching them all at once.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "current_build_id",
            UUID(as_uuid=True),
            sa.ForeignKey("builds.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_agents_current_build_id", "agents", ["current_build_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_agents_current_build_id", table_name="agents")
    op.drop_column("agents", "current_build_id")
