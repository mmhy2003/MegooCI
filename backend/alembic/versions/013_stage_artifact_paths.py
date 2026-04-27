"""Add artifact_paths to stages table.

Revision ID: 013
Revises: 012
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "stages",
        sa.Column("artifact_paths", ARRAY(sa.String(500)), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stages", "artifact_paths")
