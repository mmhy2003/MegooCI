"""Add runs_on to stages and enabled flag to agents.

Revision ID: 019
Revises: 018
Create Date: 2026-05-21

Initial cut of the agent-targeting feature: a per-stage ``runs_on``
declaration and an ``agents.enabled`` flag. The stage-level shape was
revisited shortly after and replaced by a pipeline-level declaration —
see migration 020 for the delta.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "stages",
        sa.Column("runs_on", JSONB, nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column(
            "enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "enabled")
    op.drop_column("stages", "runs_on")
