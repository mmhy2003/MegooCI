"""Add ON DELETE CASCADE to notification_deliveries.build_id

Revision ID: 022
Revises: 021
Create Date: 2026-06-23

A build's notification deliveries must vanish with the build. Without this,
deleting a pipeline (which cascades to its builds) aborts on the
notification_deliveries.build_id FK, so a pipeline with any notified build
cannot be deleted at all.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FK = "notification_deliveries_build_id_fkey"


def upgrade() -> None:
    op.drop_constraint(_FK, "notification_deliveries", type_="foreignkey")
    op.create_foreign_key(
        _FK, "notification_deliveries", "builds",
        ["build_id"], ["id"], ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(_FK, "notification_deliveries", type_="foreignkey")
    op.create_foreign_key(
        _FK, "notification_deliveries", "builds",
        ["build_id"], ["id"],
    )
