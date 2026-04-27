"""Add artifact permissions to system roles.

Revision ID: 011
Revises: 010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("name", sa.String),
        sa.column("permissions", ARRAY(sa.String(100))),
    )

    # Admin — full access
    op.execute(
        roles.update()
        .where(roles.c.name == "admin")
        .values(
            permissions=sa.func.array_cat(
                roles.c.permissions,
                sa.cast(
                    sa.literal_column("'{artifacts.read,artifacts.manage}'"),
                    ARRAY(sa.String(100)),
                ),
            )
        )
    )

    # Developer — full access
    op.execute(
        roles.update()
        .where(roles.c.name == "developer")
        .values(
            permissions=sa.func.array_cat(
                roles.c.permissions,
                sa.cast(
                    sa.literal_column("'{artifacts.read,artifacts.manage}'"),
                    ARRAY(sa.String(100)),
                ),
            )
        )
    )

    # Viewer — read-only
    op.execute(
        roles.update()
        .where(roles.c.name == "viewer")
        .values(
            permissions=sa.func.array_cat(
                roles.c.permissions,
                sa.cast(
                    sa.literal_column("'{artifacts.read}'"),
                    ARRAY(sa.String(100)),
                ),
            )
        )
    )


def downgrade() -> None:
    roles = sa.table(
        "roles",
        sa.column("name", sa.String),
        sa.column("permissions", ARRAY(sa.String(100))),
    )

    for role_name in ("admin", "developer", "viewer"):
        op.execute(
            roles.update()
            .where(roles.c.name == role_name)
            .values(
                permissions=sa.func.array_remove(
                    sa.func.array_remove(
                        roles.c.permissions, "artifacts.read"
                    ),
                    "artifacts.manage",
                )
            )
        )
