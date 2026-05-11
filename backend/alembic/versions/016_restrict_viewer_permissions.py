"""Remove artifacts, registry, and agents read from viewer role

Revision ID: 016
Revises: 015
Create Date: 2026-05-11

Viewers should not see the Artifacts, Registry, or Agents sidebar menus.
Strip the corresponding read permissions so the frontend permission gate
hides these items.

Integrations and Users are already hidden via adminOnly guards.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PERMISSIONS_TO_REMOVE = ["artifacts.read", "registry.read", "agents.read"]


def upgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("name", sa.String),
        sa.column("permissions", ARRAY(sa.String(100))),
    )

    # Nest array_remove calls to strip all three permissions in one UPDATE.
    expr = roles.c.permissions
    for perm in PERMISSIONS_TO_REMOVE:
        expr = sa.func.array_remove(expr, perm)

    op.execute(
        roles.update()
        .where(roles.c.name == "viewer")
        .values(permissions=expr)
    )


def downgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("name", sa.String),
        sa.column("permissions", ARRAY(sa.String(100))),
    )

    # Re-add the three permissions.
    op.execute(
        roles.update()
        .where(roles.c.name == "viewer")
        .values(
            permissions=sa.func.array_cat(
                roles.c.permissions,
                sa.cast(
                    sa.literal_column(
                        "'{artifacts.read,registry.read,agents.read}'"
                    ),
                    ARRAY(sa.String(100)),
                ),
            )
        )
    )
