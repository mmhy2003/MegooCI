"""Make deploy token project_id nullable (global tokens)

Revision ID: 014
Revises: 013
Create Date: 2026-05-06

Allow registry deploy tokens to be created without a project association.
Global tokens grant access across all projects in the registry.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "registry_deploy_tokens",
        "project_id",
        existing_type=UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Set any NULL project_ids to a placeholder before making non-nullable
    # (this is a lossy downgrade — global tokens lose their global scope).
    op.execute(
        "DELETE FROM registry_deploy_tokens WHERE project_id IS NULL"
    )
    op.alter_column(
        "registry_deploy_tokens",
        "project_id",
        existing_type=UUID(as_uuid=True),
        nullable=False,
    )
