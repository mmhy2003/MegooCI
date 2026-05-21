"""Move runs_on from stages to builds.

Revision ID: 020
Revises: 019
Create Date: 2026-05-21

``runs_on`` was originally declared per-stage in the YAML and stored on
the ``stages`` table. It was reworked to be a pipeline-level field that
applies to the whole build — the executor only ever claims one agent
per build, so per-stage routing was fake flexibility. This migration
follows the YAML reshape: drop the column from ``stages``, add it to
``builds``. Both columns are nullable; existing rows pick up NULL,
which the dispatcher interprets as "any online agent".
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "builds",
        sa.Column("runs_on", JSONB, nullable=True),
    )
    op.drop_column("stages", "runs_on")


def downgrade() -> None:
    op.add_column(
        "stages",
        sa.Column("runs_on", JSONB, nullable=True),
    )
    op.drop_column("builds", "runs_on")
