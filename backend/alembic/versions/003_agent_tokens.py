"""Agent registration tokens

Revision ID: 003
Revises: 002
Create Date: 2026-04-20

Adds persistent token columns to the `agents` table so the `megooci-agent`
binary can authenticate to the controller without piggybacking on a user
JWT. Tokens are stored as bcrypt hashes; a 12-char prefix is kept in cleartext
for UI/log display so admins can tell tokens apart.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("token_hash", sa.String(255), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column("token_prefix", sa.String(32), nullable=True),
    )
    op.add_column(
        "agents",
        sa.Column(
            "token_issued_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "agent_version", sa.String(64), nullable=True
        ),
    )
    op.add_column(
        "agents",
        sa.Column(
            "connected_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "connected_at")
    op.drop_column("agents", "agent_version")
    op.drop_column("agents", "token_issued_at")
    op.drop_column("agents", "token_prefix")
    op.drop_column("agents", "token_hash")
