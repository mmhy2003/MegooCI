"""Make the database's configuration equal to a backup's.

Everything here runs in the caller's transaction and never commits: the
caller commits when the whole restore has worked, and rolls back otherwise,
which leaves the server exactly as it was.

History — builds, logs, deliveries, the audit trail — is not in a backup. It
is kept for everything that still exists afterwards. For configuration the
restore removes, the database's own rules decide where they exist (a removed
pipeline takes its builds with it); where they do not, the reference is
cleared when it can be empty, and the history row is removed when it cannot.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401 — every table must be registered on the metadata
from app.models.agent import Agent
from app.models.base import Base
from app.models.build import Build, Stage, Step
from app.models.user import User
from app.services.backup.tables import (
    TABLES,
    TableSpec,
    decode_row,
    is_server_state,
    server_fernet,
)

_CONFIG_TABLES = {spec.name for spec in TABLES}
_CHUNK = 500


def _chunks(keys: list[Any]) -> list[list[Any]]:
    return [keys[i:i + _CHUNK] for i in range(0, len(keys), _CHUNK)]


def _is_leaf(spec: TableSpec) -> bool:
    """True when no other configuration table points at this one."""
    return not any(
        fk.column.table is spec.table
        for other in TABLES
        if other.table is not spec.table
        for fk in other.table.foreign_keys
    )


def _storable(spec: TableSpec, row: dict[str, Any]) -> dict[str, Any]:
    """*row* as it is handed to the database. An empty JSON column must be
    written as SQL NULL: given a plain ``None``, a JSON column stores the JSON
    value ``null`` instead, which the database does not treat as empty."""
    return {
        name: sa.null() if value is None and isinstance(spec.table.c[name].type, sa.JSON) else value
        for name, value in row.items()
    }


def _self_references(spec: TableSpec) -> list[sa.Column]:
    return [fk.parent for fk in spec.table.foreign_keys if fk.column.table is spec.table]


def _parents_first(spec: TableSpec, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*rows* ordered so that a row follows the row of the same table it
    points at (a sub-project follows its parent project)."""
    columns = [column.name for column in _self_references(spec)]
    if not columns:
        return rows
    by_key = {row[spec.key]: row for row in rows}
    ordered: list[dict[str, Any]] = []
    placed: set[Any] = set()

    def place(row: dict[str, Any], trail: frozenset = frozenset()) -> None:
        key = row[spec.key]
        if key in placed or key in trail:
            return
        for column in columns:
            parent = by_key.get(row.get(column))
            if parent is not None:
                place(parent, trail | {key})
        placed.add(key)
        ordered.append(row)

    for row in rows:
        place(row)
    return ordered


async def _keep_acting_admin(
    db: AsyncSession, users: list[dict[str, Any]], acting_user_id: uuid.UUID
) -> None:
    """The administrator who restores is never locked out: their account
    keeps its current password and stays an active administrator. Everything
    else about it comes from the backup, when the backup has it."""
    table = User.__table__
    current = (
        await db.execute(sa.select(table).where(table.c.id == acting_user_id))
    ).mappings().first()
    if current is None:
        return
    email = str(current["email"]).lower()
    account = next((row for row in users if row["id"] == current["id"]), None)
    if account is None:
        account = next((row for row in users if str(row.get("email", "")).lower() == email), None)
    if account is None:
        account = dict(current)
        users.append(account)
    account["hashed_password"] = current["hashed_password"]
    account["is_admin"] = True
    account["is_active"] = True


_AGENT_RUNTIME = ("status", "connected_at", "last_seen_at", "agent_version")


async def _keep_agent_connections(db: AsyncSession, agents: list[dict[str, Any]]) -> None:
    """Whether an agent is connected is a fact about this server, not about
    the backup. An agent that is connected now stays so: it only reports that
    it is alive and would not connect again, so writing it as never connected
    would leave it unused until someone restarted it. An agent this server has
    not seen yet starts offline. Either way nothing stays reserved for a
    build: pending builds are cancelled."""
    table = Agent.__table__
    current = {row["id"]: row for row in (await db.execute(sa.select(table))).mappings()}
    for agent in agents:
        now = current.get(agent["id"])
        for column in _AGENT_RUNTIME:
            agent[column] = now[column] if now is not None else None
        if now is None:
            agent["status"] = "offline"
        agent["current_build_id"] = None


async def _cancel_pending_builds(db: AsyncSession) -> None:
    """Builds that have not started would run against configuration that is
    about to change."""
    now = datetime.now(timezone.utc)
    pending = sa.select(Build.id).where(Build.status.in_(("pending", "queued")))
    stages = sa.select(Stage.id).where(Stage.build_id.in_(pending))
    for model, scope in ((Step, Step.stage_id.in_(stages)), (Stage, Stage.build_id.in_(pending))):
        await db.execute(
            sa.update(model)
            .where(scope, model.status.in_(("pending", "running")))
            .values(status="cancelled", finished_at=now)
        )
    await db.execute(
        sa.update(Build)
        .where(Build.status.in_(("pending", "queued")))
        .values(status="cancelled", finished_at=now)
    )


async def _detach_history(db: AsyncSession, spec: TableSpec, keys: list[Any]) -> None:
    """Before rows are removed: deal with the history that points at them and
    that the database would not deal with by itself."""
    for table in Base.metadata.tables.values():
        if table.name in _CONFIG_TABLES:
            continue
        for fk in table.foreign_keys:
            if fk.column.table is not spec.table or fk.ondelete:
                continue
            for chunk in _chunks(keys):
                if fk.parent.nullable:
                    statement = (
                        sa.update(table).where(fk.parent.in_(chunk)).values({fk.parent.name: None})
                    )
                else:
                    statement = sa.delete(table).where(fk.parent.in_(chunk))
                await db.execute(statement)


def _unique_columns(spec: TableSpec) -> list[sa.Column]:
    """The columns of a table that hold a unique name, email or slug."""
    return [
        column for column in spec.table.columns
        if column.unique and isinstance(column.type, sa.String) and not column.primary_key
    ]


async def _release(db: AsyncSession, spec: TableSpec, key: Any, columns: list[sa.Column]) -> None:
    """Give a row a placeholder in *columns*, so the value it held is free
    for another row until this one is rewritten or removed."""
    if columns:
        await db.execute(
            sa.update(spec.table)
            .where(spec.table.c[spec.key] == key)
            .values({column.name: f"~moving~{uuid.uuid4().hex}" for column in columns})
        )


async def _delete(db: AsyncSession, spec: TableSpec, keys: list[Any]) -> None:
    key = spec.table.c[spec.key]
    await _detach_history(db, spec, keys)
    for column in _self_references(spec):
        for chunk in _chunks(keys):
            await db.execute(
                sa.update(spec.table).where(key.in_(chunk)).values({column.name: None})
            )
    for chunk in _chunks(keys):
        await db.execute(sa.delete(spec.table).where(key.in_(chunk)))


async def apply_configuration(
    db: AsyncSession,
    payload: dict[str, Any],
    secret_key: str,
    *,
    acting_user_id: uuid.UUID,
) -> dict[str, dict[str, int]]:
    """Replace the configuration with *payload*'s. Returns, per table, how
    many rows were written and how many removed. Does not commit."""
    fernet = server_fernet(secret_key)
    stored = payload["tables"]
    rows: dict[str, list[dict[str, Any]]] = {}
    for spec in TABLES:
        decoded = [
            decode_row(spec, row, fernet)
            for row in stored.get(spec.name, [])
            if isinstance(row, dict)
        ]
        rows[spec.name] = [row for row in decoded if not is_server_state(spec, row)]

    await _keep_acting_admin(db, rows[User.__tablename__], acting_user_id)
    await _keep_agent_connections(db, rows[Agent.__tablename__])
    await _cancel_pending_builds(db)

    existing: dict[str, dict[Any, Any]] = {}
    doomed: dict[str, list[Any]] = {}
    for spec in TABLES:
        current = (await db.execute(sa.select(spec.table))).mappings().all()
        keep = {row[spec.key] for row in rows[spec.name]}
        existing[spec.name] = {row[spec.key]: row for row in current}
        doomed[spec.name] = [
            row[spec.key]
            for row in current
            if row[spec.key] not in keep and not is_server_state(spec, row)
        ]

    # 1. Rows nothing else in the configuration points at can go now. The
    #    others are still referenced by rows the next step rewrites, so they
    #    only give up their unique values for the moment.
    for spec in reversed(TABLES):
        if not doomed[spec.name]:
            continue
        if _is_leaf(spec):
            await _delete(db, spec, doomed[spec.name])
        else:
            for key_value in doomed[spec.name]:
                await _release(db, spec, key_value, _unique_columns(spec))

    #    Rows that stay but get another name or email give theirs up as well:
    #    rows are rewritten one at a time, and without this the order could
    #    make one take a value another still holds.
    for spec in TABLES:
        columns = _unique_columns(spec)
        for row in rows[spec.name]:
            now = existing[spec.name].get(row[spec.key])
            if now is not None:
                changing = [c for c in columns if c.name in row and row[c.name] != now[c.name]]
                await _release(db, spec, row[spec.key], changing)

    # 2. Write the backup's rows, parents first.
    for spec in TABLES:
        key = spec.table.c[spec.key]
        for row in _parents_first(spec, rows[spec.name]):
            values = _storable(spec, row)
            if row[spec.key] in existing[spec.name]:
                del values[spec.key]
                if values:
                    await db.execute(
                        sa.update(spec.table).where(key == row[spec.key]).values(values)
                    )
            else:
                await db.execute(sa.insert(spec.table).values(values))

    # 3. Nothing from the backup points at the remaining rows any more:
    #    remove them, children first.
    for spec in reversed(TABLES):
        if doomed[spec.name] and not _is_leaf(spec):
            await _delete(db, spec, doomed[spec.name])

    await db.flush()
    return {
        spec.name: {"written": len(rows[spec.name]), "removed": len(doomed[spec.name])}
        for spec in TABLES
    }
