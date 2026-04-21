"""Step action types and config

Revision ID: 004
Revises: 003
Create Date: 2026-04-21

Adds `step_type` (varchar) and `config_json` (jsonb) to the `steps` table so
each step can carry a typed action (run, docker_build, docker_push,
docker_login, git_clone, ssh_exec, wait_webhook, wait_input) plus its
type-specific configuration.  Existing rows get step_type='run' to maintain
backward compatibility.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "steps",
        sa.Column("step_type", sa.String(50), nullable=False, server_default="run"),
    )
    op.add_column(
        "steps",
        sa.Column("config_json", JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("steps", "config_json")
    op.drop_column("steps", "step_type")
