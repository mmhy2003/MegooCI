"""The backups directory: names, listing, and the one-at-a-time lock."""
import os
import time
from datetime import datetime, timezone

import pytest

from app.services.backup import format as backup_format
from app.services.backup import store
from app.services.backup.format import write_backup

WHEN = datetime(2026, 10, 8, 3, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def backups(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "backups_dir", lambda: tmp_path)
    monkeypatch.setattr(backup_format, "SCRYPT_N", 2**10)
    return tmp_path


def _data(kind="manual"):
    return write_backup({"tables": {}}, "a passphrase!", kind=kind, schema_revision="023",
                        created_at=WHEN.isoformat(), counts={"users": 3})


def test_names_carry_the_time_in_utc_and_the_kind():
    assert store.new_name("manual", WHEN) == "megooci-backup-20261008-030000-manual.mcbak"
    assert store.parse_name("megooci-backup-20261008-030000-pre-restore.mcbak") == ("pre-restore", WHEN)


def test_a_name_that_is_taken_gets_a_number():
    first = store.new_name("scheduled", WHEN)
    store.write(first, _data())
    second = store.new_name("scheduled", WHEN)
    store.write(second, _data())

    assert second == "megooci-backup-20261008-030000-scheduled-2.mcbak"
    assert store.new_name("scheduled", WHEN).endswith("-scheduled-3.mcbak")
    assert store.parse_name(second) == ("scheduled", WHEN)


@pytest.mark.parametrize("name", [
    "../megooci-backup-20261008-030000-manual.mcbak",
    "megooci-backup-20261008-030000-manual.mcbak/../../etc/passwd",
    "..\\megooci-backup-20261008-030000-manual.mcbak",
    "megooci-backup-20261008-030000-manual.mcbak.part",
    "megooci-backup-20261008-030000-weekly.mcbak",
    "megooci-backup-20261399-030000-manual.mcbak",
    "megooci-backup-20261008-030000-manual.zip",
    "backup.mcbak", ".lock", "", "megooci-backup-20261008-030000-manual.mcbak\n",
])
def test_any_other_name_is_refused_everywhere(name):
    for use in (store.parse_name, store.path_of, store.read, store.delete, store.describe):
        with pytest.raises(store.InvalidBackupName):
            use(name)
    with pytest.raises(store.InvalidBackupName):
        store.write(name, b"x")


def test_a_written_file_is_complete_and_leaves_nothing_behind(backups):
    name = store.new_name("manual", WHEN)
    store.write(name, _data())

    assert store.read(name) == (backups / name).read_bytes()
    assert sorted(os.listdir(backups)) == [name]


def test_list_is_newest_first_and_ignores_everything_else(backups):
    older = "megooci-backup-20261007-030000-scheduled.mcbak"
    newer = "megooci-backup-20261008-030000-manual.mcbak"
    store.write(older, _data("scheduled"))
    store.write(newer, _data())
    (backups / "notes.txt").write_text("not a backup")
    (backups / (newer + ".part")).write_bytes(b"half")
    (backups / "megooci-backup-20261009-030000-manual.mcbak").mkdir()

    listed = store.list_backups()

    assert [backup.name for backup in listed] == [newer, older]
    assert (listed[0].kind, listed[0].created_at) == ("manual", WHEN)
    assert listed[0].size == len(_data()) and listed[0].header.counts == {"users": 3}
    assert listed[0].error is None


def test_a_file_that_is_not_a_backup_is_listed_with_its_problem(backups):
    name = "megooci-backup-20261008-030000-uploaded.mcbak"
    (backups / name).write_bytes(b"garbage")

    (listed,) = store.list_backups()

    assert listed.header is None and listed.error == "This is not a MegooCI backup file."


def test_reading_or_deleting_a_backup_that_is_not_there():
    name = "megooci-backup-20261008-030000-manual.mcbak"
    for use in (store.read, store.delete, store.describe):
        with pytest.raises(store.BackupNotFound):
            use(name)


def test_delete_removes_the_file(backups):
    name = store.new_name("manual", WHEN)
    store.write(name, _data())
    store.delete(name)
    assert os.listdir(backups) == []


def test_only_one_backup_or_restore_at_a_time():
    with store.exclusive():
        with pytest.raises(store.Busy) as exc:
            with store.exclusive():
                pass
        assert str(exc.value) == "Another backup or restore is running."
    with store.exclusive():
        pass  # released


def test_the_lock_is_released_when_the_work_fails():
    with pytest.raises(RuntimeError):
        with store.exclusive():
            raise RuntimeError("boom")
    with store.exclusive():
        pass


def test_a_lock_left_by_a_dead_process_is_taken_over(backups):
    lock = backups / ".lock"
    lock.write_text("")
    with pytest.raises(store.Busy):
        with store.exclusive():
            pass
    old = time.time() - store.LOCK_STALE_SECONDS - 5
    os.utime(lock, (old, old))

    with store.exclusive():
        pass
    assert not lock.exists()


def test_the_lock_file_is_never_listed_as_a_backup():
    with store.exclusive():
        assert store.list_backups() == []
