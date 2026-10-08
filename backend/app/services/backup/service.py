"""The backup operations the admin API and the scheduled task call.

Each takes a session factory, not a session: a backup has to read the
configuration on a session of its own, and a restore is one transaction that
must not share a session with anything else. The scheduled task runs in a
worker with its own event loop, and passes its own factory.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.core import audit
from app.models.build import Build
from app.services.backup import remote, settings, store
from app.services.backup.export import export_configuration
from app.services.backup.format import (
    Header,
    WrongPassphrase,
    read_backup,
    read_header,
    write_backup,
)
from app.services.backup.restore import apply_configuration
from app.services.backup.schedule import Schedule, is_due, to_prune
from app.services.backup.settings import RemoteConfig
from app.services.backup.store import BackupFile

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]

CONFIRMATION_WORD = "restore"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
UNVERSIONED = "unversioned"


class Refused(Exception):
    """The operation was not started. The message says what to do about it."""


class PassphraseRequired(WrongPassphrase):
    """The stored passphrase does not open the file; the user must give the
    one the backup was made with."""


class RestoreFailed(Exception):
    """Applying the backup failed. Nothing was changed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def schema_revision(db: AsyncSession) -> str:
    """The revision of the database schema, as the migrations record it."""
    try:
        value = await db.scalar(sa.text("SELECT version_num FROM alembic_version"))
    except Exception:
        await db.rollback()
        return UNVERSIONED
    return str(value) if value else UNVERSIONED


async def record_event(
    factory: SessionFactory, action: str, actor_id: uuid.UUID | None, metadata: dict[str, Any]
) -> None:
    """Record in the audit trail. Never raises. After a restore the actor's
    row may be gone; the entry is then written without an actor."""
    for actor in (actor_id, None):
        try:
            async with factory() as db:
                await audit.record(action=action, actor_id=actor, target_type="backup",
                                   metadata=metadata, db=db)
                await db.commit()
            return
        except Exception:
            if actor is None:
                logger.warning("Could not write audit entry %s", action, exc_info=True)


# ── creating ────────────────────────────────────────────────────────────

async def _copy_to_remote(
    factory: SessionFactory, secret_key: str, name: str, data: bytes
) -> dict[str, Any] | None:
    """Upload a backup when the remote copy is on, and record how it went.
    Never raises: a remote problem must not fail the backup."""
    try:
        async with factory() as db:
            config = await settings.get_remote(db, secret_key)
        if not config.enabled:
            return None
        try:
            await remote.upload(config, name, data)
            status: dict[str, Any] = {"status": "uploaded", "error": None}
        except remote.RemoteError as exc:
            status = {"status": "failed", "error": str(exc)}
        status["at"] = _now().isoformat()
        async with factory() as db:
            state = await settings.get_state(db)
            state["remote"][name] = status
            await settings.save_state(db, state)
            await db.commit()
        return status
    except Exception:
        logger.exception("Could not record the remote copy of %s", name)
        return None


async def _create(
    factory: SessionFactory, secret_key: str, kind: str, actor_id: uuid.UUID | None, now: datetime
) -> BackupFile:
    async with factory() as db:
        passphrase = await settings.get_passphrase(db, secret_key)
        revision = await schema_revision(db)
    if not passphrase:
        raise Refused("Set a backup passphrase first, under Admin → Backups → Settings.")
    async with factory() as db:
        payload, counts = await export_configuration(db, secret_key)
    # Deriving the key takes a tenth of a second of CPU: keep it off the loop.
    data = await asyncio.to_thread(
        write_backup, payload, passphrase, kind=kind, schema_revision=revision,
        created_at=now.isoformat(), counts=counts,
    )
    name = store.new_name(kind, now)
    store.write(name, data)
    await _copy_to_remote(factory, secret_key, name, data)
    await record_event(factory, "backup.create", actor_id, {"name": name, "kind": kind})
    return store.describe(name)


async def create_backup(
    factory: SessionFactory,
    secret_key: str,
    *,
    kind: str = "manual",
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> BackupFile:
    with store.exclusive():
        return await _create(factory, secret_key, kind, actor_id, now or _now())


def store_upload(data: bytes) -> BackupFile:
    """Keep an uploaded backup file. Its name carries the time the backup was
    made, from its own header, and the kind ``uploaded``."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise Refused("The file is larger than 100 MB; a configuration backup never is.")
    header = read_header(data)
    try:
        made = datetime.fromisoformat(header.created_at)
        if made.tzinfo is None:
            made = made.replace(tzinfo=timezone.utc)
    except ValueError:
        made = _now()
    name = store.new_name("uploaded", made)
    store.write(name, data)
    return store.describe(name)


# ── restoring ───────────────────────────────────────────────────────────

async def restore_checks(factory: SessionFactory, header: Header | None) -> list[dict[str, Any]]:
    """The preconditions of a restore and whether each holds now."""
    from app.api.v1.system import is_maintenance_mode

    async with factory() as db:
        maintenance = await is_maintenance_mode(db)
        running = await db.scalar(
            sa.select(sa.func.count()).select_from(Build).where(Build.status == "running")
        )
        revision = await schema_revision(db)
    checks = [
        {"name": "maintenance", "ok": bool(maintenance),
         "message": "Maintenance mode is on." if maintenance
         else "Turn maintenance mode on first, so no build can start."},
        {"name": "builds", "ok": not running,
         "message": "No build is running." if not running
         else f"{running} build(s) are running. Wait for them to finish, or cancel them."},
    ]
    if header is not None:
        same = header.schema_revision == revision
        checks.append({
            "name": "schema", "ok": same,
            "message": "The backup matches this server's database version." if same
            else f"The backup was made with database version {header.schema_revision}; this "
                 f"server is at {revision}. Install the release the backup was made with, "
                 "restore, then upgrade.",
        })
    return checks


def _reason(exc: Exception) -> str:
    """What went wrong while applying a backup, without the values of the row
    it went wrong on. A database error's own text carries the statement's
    values — password hashes, variable values, API keys — so only the first
    line of the database's message is used: it names the constraint."""
    original = str(getattr(exc, "orig", "") or "").strip()
    if not original:
        return type(exc).__name__
    return f"{type(exc).__name__}: {original.splitlines()[0]}"[:300]


def _open(data: bytes, given: str | None, stored: str | None) -> dict[str, Any]:
    if given:
        return read_backup(data, given)[1]
    if stored:
        try:
            return read_backup(data, stored)[1]
        except WrongPassphrase:
            pass
    raise PassphraseRequired(
        "This backup was made with another passphrase. Enter the passphrase it was made with."
    )


async def restore_backup(
    factory: SessionFactory,
    secret_key: str,
    name: str,
    *,
    acting_user_id: uuid.UUID,
    confirm: str,
    passphrase: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Replace the server's configuration with the backup's.

    Raises before anything changes when a precondition is not met; if applying
    the backup fails, the transaction is rolled back and ``RestoreFailed`` is
    raised.
    """
    if confirm != CONFIRMATION_WORD:
        raise Refused(f'Type "{CONFIRMATION_WORD}" to confirm.')
    data = store.read(name)
    header = read_header(data)

    with store.exclusive():
        failed = [check for check in await restore_checks(factory, header) if not check["ok"]]
        if failed:
            raise Refused(failed[0]["message"])
        async with factory() as db:
            stored = await settings.get_passphrase(db, secret_key)
        payload = await asyncio.to_thread(_open, data, passphrase, stored)

        # The way back, should the restore turn out to be a mistake.
        before = await _create(factory, secret_key, "pre-restore", acting_user_id, now or _now())

        async with factory() as db:
            try:
                summary = await apply_configuration(
                    db, payload, secret_key, acting_user_id=acting_user_id
                )
                await db.commit()
            except Exception as exc:
                await db.rollback()
                reason = _reason(exc)
                # Deliberately without the traceback or the exception's text.
                logger.error("Restore of %s failed and was rolled back: %s", name, reason)
                raise RestoreFailed(
                    "The backup could not be applied, and nothing was changed. "
                    f"The server reported: {reason}."
                ) from None

    search_rebuilt = await _rebuild_search_index(factory)
    await record_event(factory, "backup.restore", acting_user_id,
                 {"name": name, "pre_restore": before.name})
    return {"tables": summary, "pre_restore": before.name, "search_index_rebuilt": search_rebuilt}


async def _rebuild_search_index(factory: SessionFactory) -> bool:
    try:
        from app.services import search

        # Emptied first: what the restore removed must not stay findable.
        await search.clear_all()
        async with factory() as db:
            await search.sync_all(db)
        return True
    except Exception:
        logger.warning("Search index was not rebuilt after the restore", exc_info=True)
        return False


# ── deleting and the remote copy ────────────────────────────────────────

async def _remove(factory: SessionFactory, secret_key: str, name: str) -> str | None:
    """Delete a backup locally and, when the remote copy is on, remotely.
    Returns the remote storage's error, if it had one; the local file is
    deleted either way."""
    store.delete(name)
    remote_error: str | None = None
    async with factory() as db:
        config = await settings.get_remote(db, secret_key)
        if config.enabled:
            try:
                await remote.delete(config, name)
            except remote.RemoteError as exc:
                remote_error = str(exc)
        state = await settings.get_state(db)
        state["remote"].pop(name, None)
        await settings.save_state(db, state)
        await db.commit()
    return remote_error


async def delete_backup(
    factory: SessionFactory, secret_key: str, name: str, *, actor_id: uuid.UUID | None = None
) -> str | None:
    remote_error = await _remove(factory, secret_key, name)
    await record_event(factory, "backup.delete", actor_id, {"name": name})
    return remote_error


async def retry_remote(factory: SessionFactory, secret_key: str, name: str) -> dict[str, Any]:
    data = store.read(name)
    status = await _copy_to_remote(factory, secret_key, name, data)
    if status is None:
        raise Refused("The remote copy is not turned on.")
    return status


async def check_remote(config: RemoteConfig) -> None:
    config.validate()
    await remote.check_connection(config)


# ── the schedule ────────────────────────────────────────────────────────

def _parse(moment: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(moment)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def _notify_admins(factory: SessionFactory, reason: str) -> None:
    client = None
    try:
        from app.services.in_app_notifications import get_admin_user_ids, notify_users

        client = aioredis.from_url(get_settings().MEGOOCI_REDIS_URL, decode_responses=True)
        async with factory() as db:
            await notify_users(
                db, client, user_ids=await get_admin_user_ids(db), type="backup_failed",
                title="Scheduled backup failed", body=reason,
            )
            await db.commit()
    except Exception:
        logger.warning("Could not notify administrators of a failed backup", exc_info=True)
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass


async def run_scheduled(
    factory: SessionFactory, secret_key: str, *, now: datetime | None = None
) -> str:
    """Make the scheduled backup if one is due. Returns what happened:
    ``not due``, ``busy``, ``done`` or ``failed``. Never raises."""
    now = now or _now()
    try:
        async with factory() as db:
            schedule = await settings.get_schedule(db)
            state = await settings.get_state(db)
        moments = [_parse(state.get("schedule_saved_at")),
                   _parse((state.get("last_run") or {}).get("at"))]
        since = max((moment for moment in moments if moment is not None), default=None)
        if not is_due(schedule, now, since=since):
            return "not due"

        try:
            backup = await create_backup(factory, secret_key, kind="scheduled", now=now)
            result = {"at": now.isoformat(), "ok": True, "error": None, "name": backup.name}
        except store.Busy:
            return "busy"
        except Exception as exc:
            logger.exception("Scheduled backup failed")
            result = {"at": now.isoformat(), "ok": False, "error": str(exc)[:500], "name": None}

        async with factory() as db:
            state = await settings.get_state(db)
            state["last_run"] = result
            await settings.save_state(db, state)
            await db.commit()

        if not result["ok"]:
            await _notify_admins(factory, result["error"] or "Unknown error.")
            return "failed"
        await _prune(factory, secret_key, schedule)
        return "done"
    except Exception:
        logger.exception("Scheduled backup check failed")
        return "failed"


async def _prune(factory: SessionFactory, secret_key: str, schedule: Schedule) -> None:
    scheduled = [backup.name for backup in store.list_backups() if backup.kind == "scheduled"]
    for name in to_prune(scheduled, schedule.keep):
        try:
            await _remove(factory, secret_key, name)
        except Exception:
            logger.warning("Could not delete old scheduled backup %s", name, exc_info=True)


# ── what the page shows ─────────────────────────────────────────────────

async def overview(factory: SessionFactory, secret_key: str) -> dict[str, Any]:
    async with factory() as db:
        revision = await schema_revision(db)
        passphrase_set = bool(await settings.get_passphrase(db, secret_key))
        schedule = await settings.get_schedule(db)
        config = await settings.get_remote(db, secret_key)
        state = await settings.get_state(db)

    backups = []
    for backup in store.list_backups():
        header = backup.header
        backups.append({
            "name": backup.name,
            "kind": backup.kind,
            "created_at": backup.created_at.isoformat(),
            "size": backup.size,
            "schema_revision": header.schema_revision if header else None,
            "compatible": bool(header and header.schema_revision == revision),
            "rows": sum(header.counts.values()) if header else None,
            "error": backup.error,
            "remote": state["remote"].get(backup.name),
        })
    return {
        "backups": backups,
        "schema_revision": revision,
        "passphrase_set": passphrase_set,
        "schedule": {"frequency": schedule.frequency, "time": schedule.time,
                     "weekday": schedule.weekday, "keep": schedule.keep},
        "remote": {"enabled": config.enabled, "endpoint_url": config.endpoint_url,
                   "region": config.region, "bucket": config.bucket, "prefix": config.prefix,
                   "access_key_id": config.access_key_id,
                   "secret_access_key_set": bool(config.secret_access_key)},
        "last_run": state.get("last_run"),
    }


async def update_settings(
    factory: SessionFactory,
    secret_key: str,
    *,
    passphrase: str | None = None,
    schedule: Schedule | None = None,
    remote_config: RemoteConfig | None = None,
    actor_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> None:
    """Change the given settings; the others stay. An empty secret access key
    in *remote_config* keeps the stored one."""
    changed = []
    async with factory() as db:
        if passphrase is not None:
            await settings.set_passphrase(db, secret_key, passphrase)
            changed.append("passphrase")
        if schedule is not None:
            await settings.set_schedule(db, schedule, now or _now())
            changed.append("schedule")
        if remote_config is not None:
            if not remote_config.secret_access_key:
                current = await settings.get_remote(db, secret_key)
                remote_config = replace(
                    remote_config, secret_access_key=current.secret_access_key
                )
            await settings.set_remote(db, secret_key, remote_config)
            changed.append("remote")
        await db.commit()
    if changed:
        await record_event(factory, "backup.settings", actor_id, {"changed": changed})
