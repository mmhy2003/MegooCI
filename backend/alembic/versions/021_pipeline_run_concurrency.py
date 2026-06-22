"""Enforce single-run pipeline concurrency via partial unique indexes

Revision ID: 021
Revises: 020
Create Date: 2026-06-22

A pipeline must never run two builds at once. Two partial unique indexes make
that physically enforceable: at most one `running` and at most one `pending`
build per pipeline. Pre-existing duplicates (which would make the unique index
creation fail) are reconciled first by cancelling all but the most recent.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _reconcile_duplicates(status: str) -> None:
    """Cancel all but the most recent build of `status` per pipeline, so the
    partial unique index can be created. No-op in a healthy system."""
    op.execute(
        sa.text(
            """
            UPDATE builds
            SET status = 'cancelled'
            WHERE status = :status
              AND id NOT IN (
                SELECT DISTINCT ON (pipeline_id) id
                FROM builds
                WHERE status = :status
                ORDER BY pipeline_id, created_at DESC
              )
            """
        ).bindparams(status=status)
    )


def upgrade() -> None:
    _reconcile_duplicates("running")
    _reconcile_duplicates("pending")
    op.create_index(
        "uq_one_running_build_per_pipeline", "builds", ["pipeline_id"],
        unique=True, postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index(
        "uq_one_pending_build_per_pipeline", "builds", ["pipeline_id"],
        unique=True, postgresql_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("uq_one_pending_build_per_pipeline", table_name="builds")
    op.drop_index("uq_one_running_build_per_pipeline", table_name="builds")
