"""Creating, restoring, scheduling and pruning backups, end to end against an
in-memory database and a temporary backups directory."""
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.services.backup import format as backup_format
from app.services.backup import remote, service, settings, store
from app.services.backup.format import WrongPassphrase, read_backup
from app.services.backup.schedule import Schedule
from app.services.backup.settings import RemoteConfig
from tests._backup import SECRET_KEY, insert, rows, seed_world
from tests._rbac import build_inmemory_factory

PASSPHRASE = "correct horse battery"
NOW = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
# The fixture below replaces this for most tests; one test runs the real one.
REBUILD_SEARCH_INDEX = service._rebuild_search_index
REMOTE = RemoteConfig(enabled=True, bucket="b", access_key_id="a", secret_access_key="s")


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture(autouse=True)
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "backups_dir", lambda: tmp_path)
    monkeypatch.setattr(backup_format, "SCRYPT_N", 2**10)

    async def no_search(factory):
        return True

    monkeypatch.setattr(service, "_rebuild_search_index", no_search)
    return tmp_path


@pytest.fixture
def storage(monkeypatch):
    """A stand-in for the remote storage: a dict, and a switch to make it fail."""
    class Storage:
        objects: dict = {}
        fail = None

    async def upload(config, name, data):
        if Storage.fail:
            raise remote.RemoteError(Storage.fail)
        Storage.objects[config.object_key(name)] = data

    async def delete(config, name):
        if Storage.fail:
            raise remote.RemoteError(Storage.fail)
        Storage.objects.pop(config.object_key(name), None)

    Storage.objects, Storage.fail = {}, None
    monkeypatch.setattr(remote, "upload", upload)
    monkeypatch.setattr(remote, "delete", delete)
    return Storage


async def _world(sf, *, passphrase=PASSPHRASE, maintenance=True):
    from app.models.system_setting import SystemSetting

    async with sf() as db:
        ids = await seed_world(db)
        if not maintenance:
            await db.execute(sa.update(SystemSetting.__table__)
                             .where(SystemSetting.key == "maintenance_mode").values(value="false"))
        if passphrase:
            await settings.set_passphrase(db, SECRET_KEY, passphrase)
        await db.commit()
    return ids


async def _pipeline_name(sf, ids):
    from app.models.pipeline import Pipeline

    async with sf() as db:
        return (await db.get(Pipeline, ids["pipeline"])).name


async def _rename_pipeline(sf, ids, name):
    from app.models.pipeline import Pipeline

    async with sf() as db:
        await db.execute(sa.update(Pipeline.__table__).where(Pipeline.id == ids["pipeline"])
                         .values(name=name))
        await db.commit()


async def _audit_actions(sf):
    from app.models.audit import AuditLogEntry

    async with sf() as db:
        found = [(row["action"], row["actor_id"]) for row in await rows(db, AuditLogEntry)
                 if row["action"].startswith("backup.")]
    # Audit rows have random ids: put them in a stable order, by action.
    return sorted(found, key=lambda entry: entry[0])


async def _restore(sf, name, ids, **kwargs):
    return await service.restore_backup(sf, SECRET_KEY, name, acting_user_id=ids["admin"],
                                        **{"confirm": "restore", **kwargs})


# ── creating ────────────────────────────────────────────────────────────

async def test_no_backup_without_a_passphrase(sf):
    await _world(sf, passphrase=None)

    with pytest.raises(service.Refused) as exc:
        await service.create_backup(sf, SECRET_KEY)

    assert "Set a backup passphrase first" in str(exc.value)
    assert store.list_backups() == []


async def test_a_manual_backup_is_a_file_that_opens_with_the_passphrase(sf):
    ids = await _world(sf)

    backup = await service.create_backup(sf, SECRET_KEY, actor_id=ids["admin"], now=NOW)

    assert backup.name == "megooci-backup-20261008-120000-manual.mcbak"
    assert (backup.kind, backup.header.schema_revision) == ("manual", "unversioned")
    header, payload = read_backup(store.read(backup.name), PASSPHRASE)
    assert header.counts["pipelines"] == 1 and payload["tables"]["pipelines"][0]["name"] == "deploy"
    assert await _audit_actions(sf) == [("backup.create", ids["admin"])]


async def test_only_one_at_a_time(sf):
    await _world(sf)
    with store.exclusive():
        with pytest.raises(store.Busy):
            await service.create_backup(sf, SECRET_KEY)


# ── uploads ─────────────────────────────────────────────────────────────

async def test_an_uploaded_file_is_named_after_when_it_was_made(sf):
    await _world(sf)
    made = await service.create_backup(sf, SECRET_KEY, now=NOW - timedelta(days=30))
    data = store.read(made.name)
    store.delete(made.name)

    uploaded = service.store_upload(data)

    assert uploaded.name == "megooci-backup-20260908-120000-uploaded.mcbak"
    assert uploaded.kind == "uploaded" and store.read(uploaded.name) == data


def test_an_upload_that_is_not_a_backup_or_is_too_large_is_refused(monkeypatch):
    from app.services.backup.format import BackupFormatError

    with pytest.raises(BackupFormatError):
        service.store_upload(b"definitely not a backup")
    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(service.Refused) as exc:
        service.store_upload(b"x" * 11)
    assert "larger than 100 MB" in str(exc.value)
    assert store.list_backups() == []


# ── restoring ───────────────────────────────────────────────────────────

async def test_restore_puts_the_configuration_back_and_leaves_a_way_back(sf):
    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    await _rename_pipeline(sf, ids, "changed-since")

    result = await _restore(sf, backup.name, ids, now=NOW + timedelta(hours=1))

    assert await _pipeline_name(sf, ids) == "deploy"
    assert result["tables"]["pipelines"] == {"written": 1, "removed": 0}
    assert result["search_index_rebuilt"] is True
    before = result["pre_restore"]
    assert before == "megooci-backup-20261008-130000-pre-restore.mcbak"
    _, payload = read_backup(store.read(before), PASSPHRASE)
    assert payload["tables"]["pipelines"][0]["name"] == "changed-since"
    actions = [action for action, _ in await _audit_actions(sf)]
    assert actions == ["backup.create", "backup.create", "backup.restore"]


@pytest.mark.parametrize("confirm", ["", "yes", "RESTORE", "restore "])
async def test_restore_needs_the_confirmation_word(sf, confirm):
    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)

    with pytest.raises(service.Refused) as exc:
        await _restore(sf, backup.name, ids, confirm=confirm)

    assert str(exc.value) == 'Type "restore" to confirm.'


async def test_restore_needs_maintenance_mode(sf):
    ids = await _world(sf, maintenance=False)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    await _rename_pipeline(sf, ids, "changed-since")

    with pytest.raises(service.Refused) as exc:
        await _restore(sf, backup.name, ids)

    assert "Turn maintenance mode on first" in str(exc.value)
    assert await _pipeline_name(sf, ids) == "changed-since"
    assert len(store.list_backups()) == 1, "no pre-restore backup for a restore that never started"


async def test_restore_needs_no_running_build(sf):
    from app.models.build import Build

    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    async with sf() as db:
        await insert(db, Build, pipeline_id=ids["pipeline"], number=9, status="running")
        await db.commit()

    with pytest.raises(service.Refused) as exc:
        await _restore(sf, backup.name, ids)

    assert "1 build(s) are running" in str(exc.value)


async def test_restore_needs_the_same_database_version(sf, monkeypatch):
    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)

    async def upgraded(db):
        return "024_later"

    monkeypatch.setattr(service, "schema_revision", upgraded)

    with pytest.raises(service.Refused) as exc:
        await _restore(sf, backup.name, ids)

    message = str(exc.value)
    assert "database version unversioned" in message and "this server is at 024_later" in message
    assert "Install the release the backup was made with" in message


async def test_the_checks_are_reported_one_by_one(sf):
    ids = await _world(sf, maintenance=False)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)

    checks = await service.restore_checks(sf, backup.header)

    assert [(check["name"], check["ok"]) for check in checks] == [
        ("maintenance", False), ("builds", True), ("schema", True)]


async def test_a_backup_made_with_another_passphrase_asks_for_it(sf):
    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    await _rename_pipeline(sf, ids, "changed-since")
    await service.update_settings(sf, SECRET_KEY, passphrase="a brand new passphrase")

    with pytest.raises(service.PassphraseRequired) as exc:
        await _restore(sf, backup.name, ids)
    assert "made with another passphrase" in str(exc.value)
    with pytest.raises(WrongPassphrase):
        await _restore(sf, backup.name, ids, passphrase="not it at all!!")
    assert await _pipeline_name(sf, ids) == "changed-since"
    assert len(store.list_backups()) == 1, "nothing is made until the file opens"

    await _restore(sf, backup.name, ids, passphrase=PASSPHRASE)

    assert await _pipeline_name(sf, ids) == "deploy"


async def test_a_restore_that_cannot_be_applied_changes_nothing(sf, monkeypatch):
    from app.models.pipeline import Pipeline

    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    await _rename_pipeline(sf, ids, "changed-since")

    async def half_done(db, payload, secret_key, *, acting_user_id):
        await db.execute(sa.delete(Pipeline.__table__))
        raise RuntimeError("disk full")

    monkeypatch.setattr(service, "apply_configuration", half_done)

    with pytest.raises(service.RestoreFailed) as exc:
        await _restore(sf, backup.name, ids)

    assert "nothing was changed" in str(exc.value) and "RuntimeError" in str(exc.value)
    assert "disk full" not in str(exc.value)
    assert await _pipeline_name(sf, ids) == "changed-since"


async def test_a_database_error_names_the_constraint_and_never_shows_or_logs_row_values(
        sf, monkeypatch, caplog):
    """The database's error carries the statement's values: password hashes,
    variable values, API keys. None of that may reach the page or the log."""
    from sqlalchemy.exc import IntegrityError

    ids = await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)

    async def collides(db, payload, secret_key, *, acting_user_id):
        raise IntegrityError(
            "UPDATE env_vars SET value=? WHERE id=?", ("s3cr3t-value", "an-id"),
            Exception('duplicate key value violates unique constraint "projects_name_key"\n'
                      "DETAIL:  Key (name)=(Top Secret Project) already exists."))

    monkeypatch.setattr(service, "apply_configuration", collides)

    with caplog.at_level("DEBUG"), pytest.raises(service.RestoreFailed) as exc:
        await _restore(sf, backup.name, ids)

    message = str(exc.value)
    assert 'unique constraint "projects_name_key"' in message and "nothing was changed" in message
    for leaked in ("s3cr3t-value", "Top Secret Project", "UPDATE env_vars"):
        assert leaked not in message and leaked not in caplog.text, leaked
    assert "projects_name_key" in caplog.text, "the log still says what went wrong"


class FakeSearch:
    """Just enough of the Meilisearch client: what was done to each index, in order."""

    def __init__(self):
        self.done = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def index(self, uid):
        fake = self

        class Index:
            async def delete_all_documents(self):
                fake.done.append((uid, "clear", None))

            async def add_documents(self, documents):
                fake.done.append((uid, "add", [document["id"] for document in documents]))

        return Index()


async def test_the_search_index_is_emptied_before_it_is_filled_again(sf, monkeypatch):
    """Adding documents never removes one: without emptying first, everything
    the restore removed would stay findable, as results that lead nowhere."""
    from app.services import search

    ids = await _world(sf)
    fake = FakeSearch()
    monkeypatch.setattr(search, "_get_client", lambda: fake)

    assert await REBUILD_SEARCH_INDEX(sf) is True

    cleared = [uid for uid, action, _ in fake.done if action == "clear"]
    assert sorted(cleared) == ["artifacts", "builds", "pipelines", "projects"]
    first_add = next(i for i, (_, action, _) in enumerate(fake.done) if action == "add")
    assert first_add == 4, "every index is emptied before anything is added"
    added = {uid: found for uid, action, found in fake.done if action == "add"}
    assert added["pipelines"] == [str(ids["pipeline"])]


async def test_a_search_server_that_is_down_does_not_fail_the_restore(sf, monkeypatch):
    from app.services import search

    def down():
        raise ConnectionError("meilisearch is not reachable")

    monkeypatch.setattr(search, "_get_client", down)

    assert await REBUILD_SEARCH_INDEX(sf) is False


async def test_restoring_a_backup_that_does_not_exist_or_is_not_a_name(sf):
    ids = await _world(sf)
    with pytest.raises(store.BackupNotFound):
        await _restore(sf, "megooci-backup-20261008-120000-manual.mcbak", ids)
    with pytest.raises(store.InvalidBackupName):
        await _restore(sf, "../../etc/passwd", ids)


# ── the remote copy ─────────────────────────────────────────────────────

async def _remote_state(sf):
    async with sf() as db:
        return (await settings.get_state(db))["remote"]


async def test_nothing_is_uploaded_while_the_remote_copy_is_off(sf, storage):
    await _world(sf)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    assert storage.objects == {} and await _remote_state(sf) == {}
    with pytest.raises(service.Refused) as exc:
        await service.retry_remote(sf, SECRET_KEY, backup.name)
    assert str(exc.value) == "The remote copy is not turned on."


async def test_every_backup_is_copied_when_the_remote_copy_is_on(sf, storage):
    ids = await _world(sf)
    await service.update_settings(sf, SECRET_KEY, remote_config=REMOTE)

    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    restored = await _restore(sf, backup.name, ids, now=NOW + timedelta(hours=1))

    assert set(storage.objects) == {backup.name, restored["pre_restore"]}
    assert storage.objects[backup.name] == store.read(backup.name)
    state = await _remote_state(sf)
    assert state[backup.name]["status"] == "uploaded" and state[backup.name]["error"] is None


async def test_a_failing_remote_never_fails_the_backup_and_can_be_retried(sf, storage):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, remote_config=REMOTE)
    storage.fail = "AccessDenied: no"

    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)

    assert store.read(backup.name)
    assert (await _remote_state(sf))[backup.name]["status"] == "failed"
    assert (await _remote_state(sf))[backup.name]["error"] == "AccessDenied: no"

    storage.fail = None
    status = await service.retry_remote(sf, SECRET_KEY, backup.name)

    assert status["status"] == "uploaded" and backup.name in storage.objects


async def test_deleting_a_backup_deletes_its_remote_copy(sf, storage):
    ids = await _world(sf)
    await service.update_settings(sf, SECRET_KEY, remote_config=REMOTE)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)

    error = await service.delete_backup(sf, SECRET_KEY, backup.name, actor_id=ids["admin"])

    assert error is None and storage.objects == {} and store.list_backups() == []
    assert await _remote_state(sf) == {}
    assert ("backup.delete", ids["admin"]) in await _audit_actions(sf)


async def test_a_remote_that_fails_on_delete_is_reported_and_the_file_still_goes(sf, storage):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, remote_config=REMOTE)
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    storage.fail = "SlowDown: try later"

    error = await service.delete_backup(sf, SECRET_KEY, backup.name)

    assert error == "SlowDown: try later" and store.list_backups() == []


async def test_saving_remote_settings_without_a_secret_keeps_the_stored_one(sf):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, remote_config=REMOTE)

    await service.update_settings(sf, SECRET_KEY, remote_config=RemoteConfig(
        enabled=True, bucket="other", access_key_id="a", secret_access_key=""))

    async with sf() as db:
        stored = await settings.get_remote(db, SECRET_KEY)
    assert (stored.bucket, stored.secret_access_key) == ("other", "s")


# ── the schedule ────────────────────────────────────────────────────────

DAILY = Schedule(frequency="daily", time="03:00", keep=2)


def at(day, hour=3, minute=5):
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


@pytest.fixture
def told(monkeypatch):
    reasons = []

    async def notify(factory, reason):
        reasons.append(reason)

    monkeypatch.setattr(service, "_notify_admins", notify)
    return reasons


async def _last_run(sf):
    async with sf() as db:
        return (await settings.get_state(db)).get("last_run")


async def test_nothing_happens_while_the_schedule_is_off(sf, told):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, schedule=Schedule(frequency="off"), now=at(7, 10))
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8)) == "not due"
    assert store.list_backups() == [] and await _last_run(sf) is None


async def test_a_due_backup_is_made_once_per_slot(sf, told):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, schedule=DAILY, now=at(7, 10))

    assert await service.run_scheduled(sf, SECRET_KEY, now=at(7, 10, 5)) == "not due"
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8)) == "done"
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8, 3, 10)) == "not due"

    (backup,) = store.list_backups()
    assert backup.name == "megooci-backup-20261008-030500-scheduled.mcbak"
    assert await _last_run(sf) == {"at": at(8).isoformat(), "ok": True, "error": None,
                                   "name": backup.name}
    assert told == []


async def test_a_failed_scheduled_backup_is_recorded_told_and_not_retried_every_tick(sf, told):
    await _world(sf, passphrase=None)
    await service.update_settings(sf, SECRET_KEY, schedule=DAILY, now=at(7, 10))

    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8)) == "failed"
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8, 3, 10)) == "not due"

    last = await _last_run(sf)
    assert last["ok"] is False and "Set a backup passphrase first" in last["error"]
    assert told == [last["error"]]
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(9)) == "failed", "tried again next day"


async def test_a_tick_that_finds_another_backup_running_tries_again_later(sf, told):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, schedule=DAILY, now=at(7, 10))

    with store.exclusive():
        assert await service.run_scheduled(sf, SECRET_KEY, now=at(8)) == "busy"
    assert await _last_run(sf) is None
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8, 3, 10)) == "done"


async def test_old_scheduled_backups_are_pruned_and_others_never(sf, told, storage):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, schedule=DAILY, remote_config=REMOTE,
                                  now=at(4, 10))
    manual = await service.create_backup(sf, SECRET_KEY, now=at(4, 11))

    for day in (5, 6, 7, 8):
        assert await service.run_scheduled(sf, SECRET_KEY, now=at(day)) == "done"

    names = [backup.name for backup in store.list_backups()]
    assert names == [
        "megooci-backup-20261008-030500-scheduled.mcbak",
        "megooci-backup-20261007-030500-scheduled.mcbak",
        manual.name,
    ]
    assert set(storage.objects) == set(names), "the remote copies are pruned too"
    assert set(await _remote_state(sf)) == set(names)


async def test_the_scheduled_run_never_raises(sf, told, monkeypatch):
    async def broken(db):
        raise RuntimeError("database is down")

    monkeypatch.setattr(settings, "get_schedule", broken)
    assert await service.run_scheduled(sf, SECRET_KEY, now=at(8)) == "failed"


# ── what the page shows ─────────────────────────────────────────────────

async def test_overview_lists_backups_settings_and_never_a_secret(sf, storage, monkeypatch):
    await _world(sf)
    await service.update_settings(sf, SECRET_KEY, schedule=DAILY, remote_config=REMOTE, now=at(4, 10))
    backup = await service.create_backup(sf, SECRET_KEY, now=NOW)
    (store.backups_dir() / "megooci-backup-20261001-000000-uploaded.mcbak").write_bytes(b"junk")

    overview = await service.overview(sf, SECRET_KEY)

    first, second = overview["backups"]
    assert first["name"] == backup.name and first["kind"] == "manual" and first["compatible"] is True
    assert first["remote"]["status"] == "uploaded" and first["rows"] > 10 and first["size"] > 100
    assert second["compatible"] is False and second["error"] == "This is not a MegooCI backup file."
    assert overview["passphrase_set"] is True and overview["schema_revision"] == "unversioned"
    assert overview["schedule"] == {"frequency": "daily", "time": "03:00", "weekday": 0, "keep": 2}
    assert overview["remote"] == {
        "enabled": True, "endpoint_url": "", "region": "", "bucket": "b", "prefix": "",
        "access_key_id": "a", "secret_access_key_set": True,
    }
    assert PASSPHRASE not in str(overview) and "'s'" not in str(overview["remote"])


async def test_an_incompatible_backup_is_marked(sf, monkeypatch):
    await _world(sf)
    await service.create_backup(sf, SECRET_KEY, now=NOW)

    async def upgraded(db):
        return "024_later"

    monkeypatch.setattr(service, "schema_revision", upgraded)

    overview = await service.overview(sf, SECRET_KEY)

    assert overview["backups"][0]["compatible"] is False
    assert overview["backups"][0]["schema_revision"] == "unversioned"
