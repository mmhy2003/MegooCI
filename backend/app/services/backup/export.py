"""Read the configuration out of the database."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.backup.tables import TABLES, encode_row, is_server_state, server_fernet


async def export_configuration(
    db: AsyncSession, secret_key: str
) -> tuple[dict[str, Any], dict[str, int]]:
    """The configuration as a backup payload, and the number of rows per table.

    *db* must be a session that has not been used yet: on PostgreSQL the
    tables are read in one snapshot, so the copy is consistent even while
    people keep working, and a snapshot has to be asked for before the
    session's first query.
    """
    if db.get_bind().dialect.name == "postgresql":
        await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})

    fernet = server_fernet(secret_key)
    tables: dict[str, list[dict[str, Any]]] = {}
    for spec in TABLES:
        result = await db.execute(sa.select(spec.table).order_by(spec.table.c[spec.key]))
        tables[spec.name] = [
            encode_row(spec, row, fernet)
            for row in result.mappings()
            if not is_server_state(spec, row)
        ]
    return {"tables": tables}, {name: len(rows) for name, rows in tables.items()}
