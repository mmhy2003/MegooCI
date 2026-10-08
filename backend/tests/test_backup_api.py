"""The admin backup API: who may call it, and what each refusal becomes."""
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
import sqlalchemy as sa
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import backups
from app.services.backup import format as backup_format
from app.services.backup import remote, service, settings, store
from app.services.backup.format import BackupFormatError, WrongPassphrase
from app.services.backup.schedule import InvalidSchedule
from app.services.backup.settings import InvalidSettings
from tests._backup import SECRET_KEY, rows, seed_world
from tests._rbac import build_inmemory_factory, make_role, make_user

PASSPHRASE = "correct horse battery"


# ── who may call it ─────────────────────────────────────────────────────

def test_every_route_requires_a_global_administrator():
    assert len(backups.router.routes) == 10
    for route in backups.router.routes:
        calls = [dependency.call for dependency in route.dependant.dependencies]
        assert backups.require_global_admin in calls, route.path


def test_the_routes_are_mounted_under_admin_backups():
    from app.api.v1.router import api_v1_router

    paths = {route.path for route in api_v1_router.routes if "backups" in route.path}
    assert "/admin/backups" in paths and "/admin/backups/{name}/restore" in paths
    assert all(path.startswith("/admin/backups") for path in paths)


async def test_administrators_of_the_whole_server_are_let_in():
    flagged = make_user(is_admin=True)
    by_role = make_user(global_role=make_role("admin", ["admin"]))

    assert await backups.require_global_admin(flagged) is flagged
    assert await backups.require_global_admin(by_role) is by_role


@pytest.mark.parametrize("user", [
    make_user(),
    make_user(global_role=make_role("developer", ["pipelines.manage", "secrets.read"])),
    # An admin role held for one project is not the server's administrator.
    make_user(project_roles=[(__import__("uuid").uuid4(), make_role("admin", ["admin"]))]),
])
async def test_everyone_else_gets_403(user):
    with pytest.raises(HTTPException) as exc:
        await backups.require_global_admin(user)
    assert exc.value.status_code == 403


# ── what each refusal becomes ───────────────────────────────────────────

@pytest.mark.parametrize("error, status, detail", [
    (store.InvalidBackupName("../x"), 400, "That is not a backup file name."),
    (store.BackupNotFound("x"), 404, "Backup not found."),
    (store.Busy("Another backup or restore is running."), 409, "Another backup or restore is running."),
    (service.PassphraseRequired("Enter the passphrase."), 422, "Enter the passphrase."),
    (WrongPassphrase("The passphrase is wrong or the file is damaged."), 422,
     "The passphrase is wrong or the file is damaged."),
    (BackupFormatError("This is not a MegooCI backup file."), 400, "This is not a MegooCI backup file."),
    (service.Refused("Turn maintenance mode on first."), 409, "Turn maintenance mode on first."),
    (InvalidSettings("The passphrase must be at least 12 characters long."), 400,
     "The passphrase must be at least 12 characters long."),
    (InvalidSchedule("Time must be HH:MM, for example 03:00."), 400, "Time must be HH:MM, for example 03:00."),
    (remote.RemoteError("NoSuchBucket: nope"), 502, "The remote storage reported: NoSuchBucket: nope"),
    (service.RestoreFailed("Nothing was changed."), 500, "Nothing was changed."),
])
def test_refusals_become_http_errors(error, status, detail):
    with pytest.raises(HTTPException) as exc:
        with backups._errors():
            raise error
    assert (exc.value.status_code, exc.value.detail) == (status, detail)


def test_other_errors_are_not_swallowed():
    with pytest.raises(RuntimeError):
        with backups._errors():
            raise RuntimeError("a bug")


# ── the endpoints ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def world(tmp_path, monkeypatch):
    """A seeded server with a passphrase, maintenance mode on, and its admin."""
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr(backups.database, "async_session", factory)
    monkeypatch.setattr(backups, "_secret_key", lambda: SECRET_KEY)
    monkeypatch.setattr(store, "backups_dir", lambda: tmp_path)
    monkeypatch.setattr(backup_format, "SCRYPT_N", 2**10)

    async def no_search(session_factory):
        return True

    monkeypatch.setattr(service, "_rebuild_search_index", no_search)
    async with factory() as db:
        ids = await seed_world(db)
        await settings.set_passphrase(db, SECRET_KEY, PASSPHRASE)
        await db.commit()
    admin = make_user(is_admin=True)
    admin.id = ids["admin"]
    yield factory, ids, admin
    await engine.dispose()


class Upload:
    def __init__(self, data):
        self.data = data

    async def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]


async def _audit(factory):
    from app.models.audit import AuditLogEntry

    async with factory() as db:
        return sorted(row["action"] for row in await rows(db, AuditLogEntry)
                      if row["action"].startswith("backup."))


async def test_create_list_download_and_delete(world):
    factory, ids, admin = world

    created = await backups.create_backup(admin)
    listing = await backups.list_backups()
    download = await backups.download_backup(created["name"], admin)
    deleted = await backups.delete_backup(created["name"], admin)

    assert [backup["name"] for backup in listing["backups"]] == [created["name"]]
    assert listing["passphrase_set"] is True and listing["backups"][0]["kind"] == "manual"
    assert download.body.startswith(b"MEGOOCI-BACKUP\n")
    assert download.headers["content-disposition"] == f'attachment; filename="{created["name"]}"'
    assert download.media_type == "application/octet-stream"
    assert deleted == {"deleted": created["name"], "remote_error": None}
    assert (await backups.list_backups())["backups"] == []
    assert await _audit(factory) == ["backup.create", "backup.delete", "backup.download"]


async def test_upload_stores_a_valid_file_and_refuses_anything_else(world):
    factory, ids, admin = world
    created = await backups.create_backup(admin)
    data = store.read(created["name"])
    store.delete(created["name"])

    uploaded = await backups.upload_backup(Upload(data), admin)

    assert uploaded["name"].endswith("-uploaded.mcbak") and store.read(uploaded["name"]) == data
    with pytest.raises(HTTPException) as exc:
        await backups.upload_backup(Upload(b"not a backup"), admin)
    assert exc.value.status_code == 400
    assert "backup.upload" in await _audit(factory)


async def test_an_oversized_upload_is_refused_without_reading_all_of_it(world, monkeypatch):
    factory, ids, admin = world
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 16)
    asked = []

    class Huge(Upload):
        async def read(self, size=-1):
            asked.append(size)
            return b"x" * size

    with pytest.raises(HTTPException) as exc:
        await backups.upload_backup(Huge(b""), admin)

    assert exc.value.status_code == 409 and asked == [17]


async def test_restore_and_its_checks(world):
    from app.models.pipeline import Pipeline

    factory, ids, admin = world
    created = await backups.create_backup(admin)
    async with factory() as db:
        await db.execute(sa.update(Pipeline.__table__).values(name="changed-since"))
        await db.commit()

    checks = await backups.restore_checks(created["name"])
    with pytest.raises(HTTPException) as unconfirmed:
        await backups.restore_backup(created["name"], backups.RestoreBody(confirm="yes"), admin)
    result = await backups.restore_backup(
        created["name"], backups.RestoreBody(confirm="restore"), admin)

    assert checks["ready"] is True and [c["name"] for c in checks["checks"]] == [
        "maintenance", "builds", "schema"]
    assert unconfirmed.value.status_code == 409
    assert result["pre_restore"].endswith("-pre-restore.mcbak")
    async with factory() as db:
        assert (await db.get(Pipeline, ids["pipeline"])).name == "deploy"


async def test_restore_is_refused_outside_maintenance_mode(world):
    from app.models.system_setting import SystemSetting

    factory, ids, admin = world
    created = await backups.create_backup(admin)
    async with factory() as db:
        await db.execute(sa.update(SystemSetting.__table__)
                         .where(SystemSetting.key == "maintenance_mode").values(value="false"))
        await db.commit()

    checks = await backups.restore_checks(created["name"])
    with pytest.raises(HTTPException) as exc:
        await backups.restore_backup(created["name"], backups.RestoreBody(confirm="restore"), admin)

    assert checks["ready"] is False
    assert exc.value.status_code == 409 and "maintenance mode" in exc.value.detail


async def test_restore_asks_for_the_passphrase_with_422(world):
    factory, ids, admin = world
    created = await backups.create_backup(admin)
    await backups.update_settings(backups.SettingsBody(passphrase="another passphrase!"), admin)

    with pytest.raises(HTTPException) as exc:
        await backups.restore_backup(created["name"], backups.RestoreBody(confirm="restore"), admin)
    assert exc.value.status_code == 422

    result = await backups.restore_backup(
        created["name"], backups.RestoreBody(confirm="restore", passphrase=PASSPHRASE), admin)
    assert "tables" in result


@pytest.mark.parametrize("name, status", [
    ("megooci-backup-20200101-000000-manual.mcbak", 404),
    ("..%2F..%2Fetc%2Fpasswd", 400),
    ("../secrets.mcbak", 400),
])
async def test_names_that_do_not_exist_or_are_not_names(world, name, status):
    factory, ids, admin = world
    for call in (lambda: backups.download_backup(name, admin),
                 lambda: backups.delete_backup(name, admin),
                 lambda: backups.restore_checks(name),
                 lambda: backups.upload_remote(name),
                 lambda: backups.restore_backup(name, backups.RestoreBody(confirm="restore"), admin)):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == status


async def test_settings_are_saved_validated_and_returned_without_secrets(world):
    factory, ids, admin = world

    overview = await backups.update_settings(backups.SettingsBody(
        schedule=backups.ScheduleBody(frequency="weekly", time="04:30", weekday=6, keep=3),
        remote=backups.RemoteBody(enabled=True, bucket="b", access_key_id="AKIA",
                                  secret_access_key="very-secret"),
    ), admin)

    assert overview["schedule"] == {"frequency": "weekly", "time": "04:30", "weekday": 6, "keep": 3}
    assert overview["remote"]["secret_access_key_set"] is True
    assert "very-secret" not in str(overview) and PASSPHRASE not in str(overview)
    assert "backup.settings" in await _audit(factory)
    for body in (backups.SettingsBody(passphrase="short"),
                 backups.SettingsBody(schedule=backups.ScheduleBody(frequency="hourly")),
                 backups.SettingsBody(remote=backups.RemoteBody(enabled=True))):
        with pytest.raises(HTTPException) as exc:
            await backups.update_settings(body, admin)
        assert exc.value.status_code == 400


async def test_testing_remote_settings_uses_the_stored_secret_when_none_is_given(world, monkeypatch):
    factory, ids, admin = world
    tried = []

    async def check(config):
        tried.append(config)
        if config.bucket == "missing":
            raise remote.RemoteError("NoSuchBucket: nope")

    monkeypatch.setattr(remote, "check_connection", check)
    await backups.update_settings(backups.SettingsBody(remote=backups.RemoteBody(
        enabled=False, bucket="b", access_key_id="AKIA", secret_access_key="stored-secret")), admin)

    ok = await backups.check_remote_settings(backups.RemoteBody(bucket="b", access_key_id="AKIA"))
    with pytest.raises(HTTPException) as exc:
        await backups.check_remote_settings(backups.RemoteBody(
            bucket="missing", access_key_id="AKIA", secret_access_key="typed"))

    assert ok == {"ok": True}
    assert (tried[0].secret_access_key, tried[0].enabled) == ("stored-secret", True)
    assert tried[1].secret_access_key == "typed"
    assert exc.value.status_code == 502 and "NoSuchBucket" in exc.value.detail


async def test_retrying_the_remote_copy_when_it_is_off_is_a_409(world):
    factory, ids, admin = world
    created = await backups.create_backup(admin)
    with pytest.raises(HTTPException) as exc:
        await backups.upload_remote(created["name"])
    assert exc.value.status_code == 409


# ── the scheduled task ──────────────────────────────────────────────────

def test_the_scheduled_task_runs_the_check_with_its_own_engine(monkeypatch):
    from app.tasks import backup_tasks
    from app.tasks.celery_app import celery_app

    seen = {}

    async def run_scheduled(factory, secret_key):
        from app import database

        seen["own_factory"] = factory is not database.async_session
        seen["secret_key"] = secret_key
        return "not due"

    monkeypatch.setattr(service, "run_scheduled", run_scheduled)

    assert backup_tasks.scheduled_backup.run() == {"outcome": "not due"}
    assert seen["own_factory"] is True and seen["secret_key"]
    entry = celery_app.conf.beat_schedule["scheduled-backup"]
    assert entry["task"] == "megooci.scheduled_backup"
    assert entry["schedule"].total_seconds() == 300
