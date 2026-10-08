# Configuration Backup and Restore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an administrator back up the server's configuration to a passphrase-encrypted file, restore one, schedule backups with retention, and optionally copy each backup to S3-compatible storage.

**Architecture:** A new backend package, `app.services.backup`, in small modules: the file format; the list of configuration tables and how a row is written; the export; the restore (one transaction, rows as plain mappings through the table objects); the backups directory; settings and schedule; the remote copy; and a service module that composes them. A thin admin-only router calls the service, and a Celery Beat task checks every five minutes whether a scheduled backup is due. Backups are files on the storage volume, not database rows, and settings live in the existing system settings table, so there is no migration. The frontend gets one new admin page.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 async (Core statements for export and restore), `cryptography` (scrypt, AES-256-GCM, Fernet), `boto3` for S3, Celery Beat, pytest with `asyncio_mode=auto` and in-memory SQLite with foreign keys on (`tests/_rbac.py`); Next.js + TypeScript for the page.

**Spec:** `docs/superpowers/specs/2026-10-08-backup-restore-design.md`

## Global Constraints

- **Branch:** all work happens on `feat/backup-restore`. Never commit to `main`.
- **Commits:** conventional style (`feat(backup): …`, `docs(backup): …`). End every commit message with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **Administrators of the whole server only.** Every route depends on `require_global_admin`, which uses `has_global_permission(user, "admin")`. Do not use `get_current_admin_user` here: it accepts an admin role held for a single project.
- **Configuration only.** The tables in `tables.TABLES` and nothing else. Builds, logs, artifacts, registry images, the audit trail, delivery history, in-app notifications and invites are never in a backup.
- **Server state is never in a backup and never touched by a restore:** system settings whose key starts with `backup_`, and `maintenance_mode` and `maintenance_message`.
- **A backup needs the passphrase** (at least 12 characters, stored encrypted with the server key, never returned by the API). No backup is made until it is set.
- **The file:** `MEGOOCI-BACKUP\n`, a 4-byte header length, a JSON header, then gzip'd JSON encrypted with AES-256-GCM; key from scrypt (`n=2**15, r=8, p=1`); the header is authenticated with the body. Format version 1.
- **File names** are `megooci-backup-YYYYMMDD-HHMMSS-<kind>[-N].mcbak` in UTC, kind one of `manual`, `scheduled`, `pre-restore`, `uploaded`. A name from a request is matched against this pattern before any use and is never joined into a path otherwise.
- **A restore** needs: a global administrator, maintenance mode on, no running build, the same schema revision, a passphrase that opens the file, and the word `restore`. It takes a `pre-restore` backup first, applies everything in one transaction, and on any error rolls back and changes nothing.
- **The administrator who restores** keeps their current password and stays an active administrator.
- **One backup or restore at a time,** by a lock file in the backups directory (the web server and the Celery worker are different processes).
- **The remote copy never fails a backup.** A remote error is recorded per file and can be retried.
- **Schedule times are UTC.** A scheduled backup is due when a slot has passed since the later of the last scheduled run and the last time the schedule was saved.
- **Celery:** the scheduled task creates its own async engine and session factory and disposes the engine; it must never use `app.database.async_session` (that engine is bound to another event loop).
- **No database migration, no new setting in `config.py`.**
- **Backend tests:** run from `backend/` with `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`. The suite has 726 tests before this plan and 900 after it.
- **Slow key derivation in tests:** tests that write backup files lower `format.SCRYPT_N` with `monkeypatch`, as the fixtures in this plan do.
- **Frontend gate:** `npx tsc --noEmit` from `frontend/`. There is no frontend unit-test harness, and `npm run lint` is broken; do not use it.
- **Colours:** theme tokens `success`, `warning`, `destructive` (`frontend/COLORS.md`).
- **Line endings:** existing files use CRLF in the working tree. Keep them: use the Edit tool, not a script that rewrites a file with LF.
- **Edits are exact.** Where a step says "Replace … with …", the first block occurs exactly once in the file; replace that text and nothing else.
- **Paths in commands:** `git` commands use paths relative to the repository root. Commands are written for Git Bash.

## Review Focus

Inputs the spec implies but does not spell out. Each has a test in the task that owns the code.

1. **A wrong passphrase, an altered file, a file cut short.** Expected: refused before anything changes, with one plain message. Pinned in Task 1 (`test_a_wrong_passphrase_is_refused`, `test_a_changed_header_is_refused_even_though_it_still_parses`, `test_a_file_cut_short_in_the_body_is_refused`) and Task 7 (`test_a_backup_made_with_another_passphrase_asks_for_it`).
2. **A crafted file whose header asks for an enormous key derivation.** Expected: refused; the server does not spend minutes or gigabytes. Pinned in Task 1 (`test_a_header_asking_for_an_unreasonable_key_derivation_is_refused`).
3. **A restore that fails halfway.** Expected: every table as it was, history included. Pinned in Task 3 (`test_a_restore_that_fails_midway_changes_nothing`) and Task 7 (`test_a_restore_that_cannot_be_applied_changes_nothing`).
4. **Since the backup, a new account took a removed user's email, or a new project a renamed project's name.** Expected: the restore still works and each name goes back to its owner. Pinned in Task 3 (`test_a_name_taken_by_something_newer_goes_back_to_its_owner`).
5. **Restoring an old backup that does not know the current administrator, or knows them with an old password.** Expected: they can still sign in as an administrator. Pinned in Task 3 (three tests under "the administrator who restores").
6. **A restore that would switch maintenance mode off or change the backup schedule, because the backup carries those rows.** Expected: both untouched. Pinned in Task 3 (`test_server_state_settings_are_never_touched`).
7. **A file name from a request that is a path, or ends in a newline.** Expected: 400, and no file outside the backups directory is ever read, written or deleted. Pinned in Task 4 (`test_any_other_name_is_refused_everywhere`) and Task 8 (`test_names_that_do_not_exist_or_are_not_names`).
8. **A user whose admin role is scoped to one project.** Expected: 403 on every route. Pinned in Task 8 (`test_everyone_else_gets_403`, `test_every_route_requires_a_global_administrator`).
9. **The remote storage is down, or rejects the keys.** Expected: the backup is still made; the failure is shown and can be retried. Pinned in Task 7 (`test_a_failing_remote_never_fails_the_backup_and_can_be_retried`).
10. **The server was down for days, or the scheduled backup keeps failing.** Expected: one backup on return, not one per missed slot; a failure is retried at the next slot, not every five minutes. Pinned in Task 5 (`test_a_server_that_was_down_for_days_makes_one_backup_not_several`) and Task 7 (`test_a_failed_scheduled_backup_is_recorded_told_and_not_retried_every_tick`).

**Known limitation, by design (in the spec):** if two rows that both exist in the backup have exchanged a unique value since it was made (two projects swapped names), the restore fails cleanly and changes nothing. It is not handled further in this plan.

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `backend/app/services/backup/__init__.py` | Create | Package description. |
| `backend/app/services/backup/format.py` | Create | The file: header, key derivation, encryption. |
| `backend/app/services/backup/tables.py` | Create | Which tables are configuration; a row to JSON and back; server-state keys. |
| `backend/app/services/backup/export.py` | Create | Read the configuration out of the database. |
| `backend/app/services/backup/restore.py` | Create | Make the configuration equal to a backup's, in the caller's transaction. |
| `backend/app/services/backup/store.py` | Create | The backups directory: names, list, read, write, delete, lock. |
| `backend/app/services/backup/schedule.py` | Create | When a backup is due; which to prune. Pure functions. |
| `backend/app/services/backup/settings.py` | Create | Passphrase, schedule, remote settings and run state in system settings. |
| `backend/app/services/backup/remote.py` | Create | Upload, delete and connection test against S3. |
| `backend/app/services/backup/service.py` | Create | Create, upload, restore, delete, retry, scheduled run, overview. |
| `backend/app/api/v1/backups.py` | Create | The admin routes; errors to HTTP. |
| `backend/app/api/v1/router.py` | Modify | Mount the routes at `/admin/backups`. |
| `backend/app/tasks/backup_tasks.py` | Create | The periodic task. |
| `backend/app/tasks/celery_app.py` | Modify | Discover the task; beat entry every five minutes. |
| `backend/pyproject.toml` | Modify | `boto3`. |
| `frontend/src/lib/api.ts` | Modify | Types and `backupsApi`. |
| `frontend/src/components/layout/sidebar.tsx` | Modify | "Backups" link for administrators. |
| `frontend/src/app/admin/backups/page.tsx` | Create | The page. |
| `README.md` | Modify | "Backup and Restore" section. |
| `backend/tests/_backup.py` | Create | Seeding helpers: one of everything, plus history. |
| `backend/tests/test_backup_*.py` | Create | Eight test files, one per task. |

---

### Task 1: The backup file

**Files:**
- Create: `backend/app/services/backup/__init__.py`, `backend/app/services/backup/format.py`
- Test: `backend/tests/test_backup_format.py` (new)

**Interfaces:**
- Produces: `MAGIC`, `FORMAT_VERSION = 1`, `KINDS = ("manual", "scheduled", "pre-restore", "uploaded")`, `SCRYPT_N = 2**15`, `SCRYPT_R = 8`, `SCRYPT_P = 1`.
- Produces: `BackupFormatError(Exception)` and `WrongPassphrase(BackupFormatError)`; their messages are shown to the user.
- Produces: `Header(format, created_at, kind, schema_revision, counts, kdf, nonce)`.
- Produces: `write_backup(payload: dict, passphrase: str, *, kind, schema_revision, created_at, counts) -> bytes`.
- Produces: `read_header(data: bytes) -> Header` (no passphrase) and `read_backup(data: bytes, passphrase: str) -> tuple[Header, dict]`. The payload is a dict with a `tables` dict.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backup_format.py`:

```python
"""The backup file: readable header, passphrase-encrypted body."""
import json

import pytest

from app.services.backup import format as backup_format
from app.services.backup.format import (
    MAGIC,
    BackupFormatError,
    WrongPassphrase,
    read_backup,
    read_header,
    write_backup,
)

PASSPHRASE = "correct horse battery"
PAYLOAD = {"tables": {"users": [{"id": "1", "email": "ünïcode@example.com"}], "roles": []}}


@pytest.fixture(autouse=True)
def fast_key_derivation(monkeypatch):
    """The real cost is a tenth of a second per file; tests write many."""
    monkeypatch.setattr(backup_format, "SCRYPT_N", 2**10)


def _file(passphrase=PASSPHRASE, **overrides):
    fields = {"kind": "manual", "schema_revision": "023", "created_at": "2026-10-08T03:00:00+00:00",
              "counts": {"users": 1, "roles": 0}}
    return write_backup(PAYLOAD, passphrase, **{**fields, **overrides})


def test_round_trip():
    header, payload = read_backup(_file(), PASSPHRASE)

    assert payload == PAYLOAD
    assert (header.format, header.kind, header.schema_revision) == (1, "manual", "023")
    assert header.created_at == "2026-10-08T03:00:00+00:00"
    assert header.counts == {"users": 1, "roles": 0}


def test_the_header_is_readable_without_the_passphrase():
    header = read_header(_file(kind="scheduled"))

    assert header.kind == "scheduled" and header.counts["users"] == 1
    assert header.kdf["name"] == "scrypt" and header.kdf["n"] == 2**10


def test_the_body_does_not_contain_the_configuration_in_the_clear():
    data = _file()
    assert b"example.com" not in data and b"users\":[{" not in data


def test_two_backups_of_the_same_content_differ():
    assert _file() != _file(), "a fresh salt and nonce every time"


def test_a_wrong_passphrase_is_refused():
    with pytest.raises(WrongPassphrase) as exc:
        read_backup(_file(), "not the passphrase")
    assert str(exc.value) == "The passphrase is wrong or the file is damaged."


def test_a_changed_byte_in_the_body_is_refused():
    data = bytearray(_file())
    data[-5] ^= 0x01
    with pytest.raises(WrongPassphrase):
        read_backup(bytes(data), PASSPHRASE)


def test_a_changed_header_is_refused_even_though_it_still_parses():
    """Someone edits the header to claim another schema revision."""
    data = _file()
    assert b'"schema_revision":"023"' in data
    forged = data.replace(b'"schema_revision":"023"', b'"schema_revision":"999"')

    assert read_header(forged).schema_revision == "999"
    with pytest.raises(WrongPassphrase):
        read_backup(forged, PASSPHRASE)


@pytest.mark.parametrize("cut", [0, 5, len(MAGIC), len(MAGIC) + 2, len(MAGIC) + 10])
def test_a_file_cut_short_in_the_header_is_refused(cut):
    with pytest.raises(BackupFormatError):
        read_header(_file()[:cut])


def test_a_file_cut_short_in_the_body_is_refused():
    with pytest.raises(WrongPassphrase):
        read_backup(_file()[:-20], PASSPHRASE)


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04 a zip file", b"{\"tables\": {}}"])
def test_other_files_are_not_backups(data):
    with pytest.raises(BackupFormatError) as exc:
        read_header(data)
    assert str(exc.value) == "This is not a MegooCI backup file."


def _with_header(change):
    """A backup whose header was rewritten by *change* (so it no longer authenticates)."""
    data = _file()
    start = len(MAGIC) + 4
    length = int.from_bytes(data[len(MAGIC):start], "big")
    header = json.loads(data[start:start + length])
    change(header)
    new = json.dumps(header).encode()
    return MAGIC + len(new).to_bytes(4, "big") + new + data[start + length:]


def test_a_newer_format_is_named_as_such():
    def newer(header):
        header["format"] = 2

    with pytest.raises(BackupFormatError) as exc:
        read_header(_with_header(newer))
    assert "format 2" in str(exc.value) and "different version of MegooCI" in str(exc.value)


@pytest.mark.parametrize(
    "field, value",
    [("n", 2**30), ("n", 1000), ("n", 0), ("r", 1000), ("p", 0), ("name", "pbkdf2")],
)
def test_a_header_asking_for_an_unreasonable_key_derivation_is_refused(field, value):
    """A crafted file must not make the server spend minutes or gigabytes."""
    def change(header):
        header["kdf"][field] = value

    with pytest.raises(BackupFormatError) as exc:
        read_header(_with_header(change))
    assert "header cannot be read" in str(exc.value)


@pytest.mark.parametrize("missing", ["kdf", "nonce", "counts", "created_at", "schema_revision"])
def test_a_header_missing_a_field_is_refused(missing):
    def change(header):
        del header[missing]

    with pytest.raises(BackupFormatError):
        read_header(_with_header(change))


def test_a_header_that_claims_to_be_huge_is_refused():
    data = MAGIC + (10**9).to_bytes(4, "big") + b"{}"
    with pytest.raises(BackupFormatError):
        read_header(data)


def test_the_real_cost_parameters_are_the_defaults(monkeypatch):
    monkeypatch.undo()
    assert (backup_format.SCRYPT_N, backup_format.SCRYPT_R, backup_format.SCRYPT_P) == (2**15, 8, 1)
```

- [ ] **Step 2: Run the tests and see them fail**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_format.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.backup'`.

- [ ] **Step 3: Write the package and the format**

Create `backend/app/services/backup/__init__.py`:

```python
"""Configuration backup and restore.

A backup is one encrypted file holding the server's configuration: users,
roles, projects, pipelines, secrets, channels, agents and settings — not build
history, logs, artifacts or registry images.

- ``format``    — the file: a readable header and a passphrase-encrypted body
- ``tables``    — which tables are configuration, and how a row is written
- ``export``    — read the configuration out of the database
- ``restore``   — make the database's configuration equal to a backup's
- ``store``     — the backups directory: names, listing, reading, writing
- ``settings``  — passphrase, schedule, remote storage and run state
- ``schedule``  — when a scheduled backup is due, and which ones to prune
- ``remote``    — the optional copy to S3-compatible storage
- ``service``   — the operations the API and the scheduled task call
"""
```

Create `backend/app/services/backup/format.py`:

```python
"""The backup file.

    MEGOOCI-BACKUP\\n | header length (4 bytes) | header (JSON) | body

The header is not secret: it lets a backup be listed, and an incompatible one
refused, without the passphrase. The body is the configuration as gzip'd JSON,
encrypted with AES-256-GCM under a key derived from the passphrase with
scrypt. The header is authenticated together with the body, so neither can be
changed without the file failing to open.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"MEGOOCI-BACKUP\n"
FORMAT_VERSION = 1
KINDS = ("manual", "scheduled", "pre-restore", "uploaded")

# scrypt cost. About a tenth of a second and 32 MiB per attempt, which makes
# guessing a passphrase offline slow. Stored in each header, so files written
# with other values still open.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

# A header is a few hundred bytes. Larger means the file is not ours.
_MAX_HEADER_BYTES = 64 * 1024
# Bounds on the cost a header may ask for, so a crafted file cannot make the
# server spend minutes or gigabytes deriving a key.
_MAX_SCRYPT_N = 2**20
_MAX_SCRYPT_R = 16
_MAX_SCRYPT_P = 4


class BackupFormatError(Exception):
    """The file cannot be read as a backup. The message is for the user."""


class WrongPassphrase(BackupFormatError):
    """The passphrase does not open the file, or the file was altered."""


@dataclass(frozen=True)
class Header:
    format: int
    created_at: str  # ISO 8601, UTC
    kind: str
    schema_revision: str
    counts: dict[str, int]
    kdf: dict[str, Any]
    nonce: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "created_at": self.created_at,
            "kind": self.kind,
            "schema_revision": self.schema_revision,
            "counts": self.counts,
            "kdf": self.kdf,
            "nonce": self.nonce,
        }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive_key(passphrase: str, kdf: dict[str, Any]) -> bytes:
    return Scrypt(
        salt=base64.b64decode(kdf["salt"]), length=32, n=kdf["n"], r=kdf["r"], p=kdf["p"]
    ).derive(passphrase.encode("utf-8"))


def write_backup(
    payload: dict[str, Any],
    passphrase: str,
    *,
    kind: str,
    schema_revision: str,
    created_at: str,
    counts: dict[str, int],
) -> bytes:
    """The bytes of a backup file holding *payload*."""
    kdf = {
        "name": "scrypt",
        "salt": _b64(os.urandom(16)),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
    }
    nonce = os.urandom(12)
    header = Header(
        format=FORMAT_VERSION,
        created_at=created_at,
        kind=kind,
        schema_revision=schema_revision,
        counts=dict(counts),
        kdf=kdf,
        nonce=_b64(nonce),
    )
    header_bytes = json.dumps(header.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    plaintext = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    body = AESGCM(_derive_key(passphrase, kdf)).encrypt(nonce, plaintext, header_bytes)
    return MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + body


def _split(data: bytes) -> tuple[bytes, bytes]:
    """The header bytes and the body bytes of a backup file."""
    if not data.startswith(MAGIC):
        raise BackupFormatError("This is not a MegooCI backup file.")
    start = len(MAGIC) + 4
    length = int.from_bytes(data[len(MAGIC):start], "big")
    if len(data) < start or not 0 < length <= _MAX_HEADER_BYTES or len(data) < start + length:
        raise BackupFormatError("The backup file is damaged: its header is incomplete.")
    return data[start:start + length], data[start + length:]


def _parse_header(header_bytes: bytes) -> Header:
    try:
        raw = json.loads(header_bytes)
        if raw["format"] != FORMAT_VERSION:
            raise BackupFormatError(
                f"This backup uses format {raw['format']!r}; this server reads format "
                f"{FORMAT_VERSION}. It was made by a different version of MegooCI."
            )
        kdf = raw["kdf"]
        counts = raw["counts"]
        header = Header(
            format=raw["format"],
            created_at=str(raw["created_at"]),
            kind=str(raw["kind"]),
            schema_revision=str(raw["schema_revision"]),
            counts={str(table): int(count) for table, count in counts.items()},
            kdf={"name": kdf["name"], "salt": str(kdf["salt"]),
                 "n": int(kdf["n"]), "r": int(kdf["r"]), "p": int(kdf["p"])},
            nonce=str(raw["nonce"]),
        )
        n, r, p = header.kdf["n"], header.kdf["r"], header.kdf["p"]
        if (
            header.kdf["name"] != "scrypt"
            or not 2 <= n <= _MAX_SCRYPT_N or n & (n - 1)
            or not 1 <= r <= _MAX_SCRYPT_R
            or not 1 <= p <= _MAX_SCRYPT_P
            or len(base64.b64decode(header.nonce)) != 12
            or not base64.b64decode(header.kdf["salt"])
        ):
            raise ValueError("unusable key-derivation parameters")
    except BackupFormatError:
        raise
    except Exception as exc:
        raise BackupFormatError("The backup file is damaged: its header cannot be read.") from exc
    return header


def read_header(data: bytes) -> Header:
    """The header of a backup file. Needs no passphrase."""
    return _parse_header(_split(data)[0])


def read_backup(data: bytes, passphrase: str) -> tuple[Header, dict[str, Any]]:
    """The header and the payload of a backup file."""
    header_bytes, body = _split(data)
    header = _parse_header(header_bytes)
    try:
        plaintext = AESGCM(_derive_key(passphrase, header.kdf)).decrypt(
            base64.b64decode(header.nonce), body, header_bytes
        )
    except InvalidTag as exc:
        raise WrongPassphrase("The passphrase is wrong or the file is damaged.") from exc
    try:
        payload = json.loads(gzip.decompress(plaintext))
    except Exception as exc:
        raise BackupFormatError("The backup file is damaged: its content cannot be read.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
        raise BackupFormatError("The backup file is damaged: it holds no configuration.")
    return header, payload
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_format.py
```

Expected: `30 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backup/__init__.py backend/app/services/backup/format.py backend/tests/test_backup_format.py
git commit -m "feat(backup): passphrase-encrypted backup file format

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The configuration tables and the export

**Files:**
- Create: `backend/app/services/backup/tables.py`, `backend/app/services/backup/export.py`
- Create: `backend/tests/_backup.py` (seeding helpers used by Tasks 2, 3, 5, 7 and 8)
- Test: `backend/tests/test_backup_tables.py` (new)

**Interfaces:**
- Produces: `TableSpec(table: sa.Table, encrypted: tuple[str, ...] = ())` with `.name` and `.key` (the single primary-key column's name).
- Produces: `TABLES: tuple[TableSpec, ...]` — sixteen tables, parents before children.
- Produces: `SERVER_STATE_PREFIX = "backup_"`, `SERVER_STATE_KEYS`, `is_server_state(spec, row) -> bool`.
- Produces: `server_fernet(secret_key: str) -> Fernet` — the cipher the server stores secrets with.
- Produces: `encode_row(spec, row, fernet) -> dict` and `decode_row(spec, data, fernet) -> dict`. A value encrypted with the server key is written as `{"$enc": "<base64 of the plain value>"}`; a value in such a column that this key cannot decrypt is carried over as it is.
- Produces: `async export_configuration(db, secret_key) -> tuple[dict, dict[str, int]]` — the payload `{"tables": {name: [rows]}}` and the row count per table. `db` must be an unused session.
- Produces, in `tests/_backup.py`: `SECRET_KEY`, `OTHER_SECRET_KEY`, `insert(db, Model, **values)`, `rows(db, Model, order_by=None)`, `encrypt`, `decrypt`, `seed_world(db, secret_key=SECRET_KEY) -> dict`, `snapshot(db) -> dict`.

- [ ] **Step 1: Write the test helpers and the failing tests**

Create `backend/tests/_backup.py`:

```python
"""Seeding helpers for the backup and restore tests."""
import itertools
import json
import uuid
from datetime import datetime, timezone

import sqlalchemy as sa

SECRET_KEY = "server-key-one"
OTHER_SECRET_KEY = "server-key-two"

_numbers = itertools.count(1)


def _filler(column):
    """A value for a required column the test did not care to give."""
    number = next(_numbers)
    kind = column.type
    if isinstance(kind, sa.Uuid):
        return uuid.uuid4()
    if isinstance(kind, sa.DateTime):
        return datetime(2026, 1, 1, tzinfo=timezone.utc)
    if isinstance(kind, sa.LargeBinary):
        return b"bytes"
    if isinstance(kind, sa.Boolean):
        return False
    if isinstance(kind, sa.Integer):
        return number
    if isinstance(kind, sa.JSON):
        return {}
    if isinstance(kind, sa.String):
        return f"{column.name}-{number}"
    raise AssertionError(f"no filler for column type {kind!r}")


async def insert(db, model, **values):
    """Insert one row of *model*, filling required columns the caller left
    out. Foreign keys must be given. Returns the row's primary key."""
    table = model.__table__
    row = dict(values)
    for column in table.columns:
        if column.name in row:
            continue
        if column.primary_key or not (
            column.nullable or column.default is not None or column.server_default is not None
        ):
            assert not column.foreign_keys, f"{table.name}.{column.name} must be given"
            row[column.name] = _filler(column)
    await db.execute(sa.insert(table).values(row))
    (key,) = table.primary_key.columns
    return row[key.name]


async def rows(db, model, order_by=None):
    """Every row of *model* as a list of dicts, in a stable order."""
    table = model.__table__
    (key,) = table.primary_key.columns
    result = await db.execute(sa.select(table).order_by(order_by if order_by is not None else key))
    return [dict(row) for row in result.mappings()]


def encrypt(plaintext: str, secret_key: str = SECRET_KEY) -> bytes:
    from app.services.backup.tables import server_fernet

    return server_fernet(secret_key).encrypt(plaintext.encode())


def decrypt(token, secret_key: str = SECRET_KEY) -> str:
    from app.services.backup.tables import server_fernet

    token = token if isinstance(token, bytes) else str(token).encode()
    return server_fernet(secret_key).decrypt(token).decode()


async def seed_world(db, secret_key: str = SECRET_KEY) -> dict:
    """One of everything a backup holds, plus history that points at it.
    Does not commit. Returns the ids."""
    from app.models.agent import Agent
    from app.models.api_token import ApiToken
    from app.models.audit import AuditLogEntry
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.git_integration import GitProviderConnection, ProjectRepository, WebhookDelivery
    from app.models.notification import NotificationChannel, NotificationDelivery
    from app.models.pipeline import Pipeline
    from app.models.project import Project
    from app.models.registry import RegistryDeployToken
    from app.models.role import Role, UserRole
    from app.models.secret import EnvVar, Secret
    from app.models.system_setting import SystemSetting
    from app.models.trigger import Trigger, WebhookEndpoint
    from app.models.user import User

    ids = {}
    ids["admin"] = await insert(db, User, email="admin@example.com", name="Admin",
                                hashed_password="hash-of-admin", is_admin=True, is_active=True)
    ids["dev"] = await insert(db, User, email="dev@example.com", name="Dev",
                              hashed_password="hash-of-dev", is_admin=False, is_active=True)
    ids["role"] = await insert(db, Role, name="developer", permissions=["pipelines.manage"])
    ids["user_role"] = await insert(db, UserRole, user_id=ids["dev"], role_id=ids["role"],
                                    scope_type="global", scope_id=None)
    ids["parent_project"] = await insert(db, Project, name="Platform", slug="platform",
                                         created_by=ids["admin"])
    ids["project"] = await insert(db, Project, name="Web", slug="web", created_by=ids["admin"],
                                  parent_id=ids["parent_project"])
    ids["connection"] = await insert(
        db, GitProviderConnection, name="github", provider_type="github",
        created_by=ids["admin"], encrypted_credential=encrypt("ghp_token", secret_key))
    ids["repository"] = await insert(
        db, ProjectRepository, project_id=ids["project"], connection_id=ids["connection"],
        created_by=ids["admin"], repo_url="https://git.example/acme/web.git", webhook_slug="slug-web",
        webhook_secret_hash=encrypt("webhook-secret", secret_key).decode())
    ids["pipeline"] = await insert(
        db, Pipeline, name="deploy", project_id=ids["project"], created_by=ids["admin"],
        project_repository_id=ids["repository"], yaml_content="name: deploy\nstages: []\n")
    ids["trigger"] = await insert(db, Trigger, pipeline_id=ids["pipeline"], type="webhook",
                                  config_json={"branch": "main"})
    # Not encrypted with the server key: must be carried over as it is.
    ids["endpoint"] = await insert(db, WebhookEndpoint, pipeline_id=ids["pipeline"],
                                   slug="hook-deploy", secret_hash="sha256:not-a-fernet-token")
    ids["secret"] = await insert(
        db, Secret, name="DEPLOY_TOKEN", scope_type="project", scope_id=ids["project"],
        created_by=ids["admin"], encrypted_payload=encrypt("s3cr3t-value", secret_key))
    ids["env_var"] = await insert(db, EnvVar, name="REGION", value="eu", scope_type="project",
                                  scope_id=ids["project"], created_by=ids["admin"])
    ids["channel"] = await insert(
        db, NotificationChannel, name="team-chat", channel_type="slack", created_by=ids["admin"],
        enabled=True,
        config_encrypted=encrypt(json.dumps({"webhook_url": "https://hooks.example/T/B/x"}), secret_key))
    ids["agent"] = await insert(db, Agent, name="linux-1", status="online", enabled=True,
                                connected_at=datetime(2026, 1, 2, tzinfo=timezone.utc))
    ids["api_token"] = await insert(db, ApiToken, user_id=ids["dev"], name="ci",
                                    token_hash="a" * 64, token_hint="mci_abcd")
    ids["deploy_token"] = await insert(db, RegistryDeployToken, name="pull", project_id=ids["project"],
                                       created_by=ids["admin"], token_hash="hash", token_hint="dt_ab")
    for key, value in (("ai_model", "gpt-test"), ("maintenance_mode", "true"),
                       ("backup_schedule", '{"frequency": "daily"}')):
        await db.execute(sa.insert(SystemSetting.__table__).values(key=key, value=value))

    # History: not in a backup, but it points at the configuration.
    ids["build"] = await insert(db, Build, pipeline_id=ids["pipeline"], number=1, status="success",
                                triggered_by=ids["dev"], trigger_type="manual")
    ids["stage"] = await insert(db, Stage, build_id=ids["build"], name="deploy", sort_order=0,
                                status="success")
    ids["step"] = await insert(db, Step, stage_id=ids["stage"], name="run", sort_order=0,
                               status="success")
    ids["log"] = await insert(db, LogChunk, step_id=ids["step"], seq=1, content="done\n",
                              timestamp=datetime(2026, 1, 3, tzinfo=timezone.utc))
    ids["delivery"] = await insert(db, NotificationDelivery, channel_id=ids["channel"],
                                   build_id=ids["build"], message="deployed")
    ids["audit"] = await insert(db, AuditLogEntry, action="pipeline.update", target_type="pipeline",
                                actor_id=ids["dev"])
    ids["webhook_delivery"] = await insert(db, WebhookDelivery, project_repository_id=ids["repository"],
                                           provider_delivery_id="d-1")
    await db.flush()
    return ids


async def snapshot(db) -> dict:
    """Every configuration table's rows, for comparing before and after."""
    from app.services.backup.tables import TABLES

    result = {}
    for spec in TABLES:
        found = await db.execute(sa.select(spec.table).order_by(spec.table.c[spec.key]))
        result[spec.name] = [dict(row) for row in found.mappings()]
    return result
```

Create `backend/tests/test_backup_tables.py`:

```python
"""Which tables a backup holds, and how a row goes into JSON and back."""
import json
import os

import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

import app.models  # noqa: F401
from app.models.base import Base
from app.services.backup.export import export_configuration
from app.services.backup.tables import (
    TABLES,
    decode_row,
    encode_row,
    is_server_state,
    server_fernet,
)
from tests._backup import OTHER_SECRET_KEY, SECRET_KEY, decrypt, seed_world
from tests._rbac import build_inmemory_factory

# Everything that is not configuration. A new table must be put on one side
# or the other on purpose: forgetting it would silently drop it from backups.
HISTORY_TABLES = {
    "builds", "stages", "steps", "log_chunks", "artifacts", "audit_log", "invites",
    "notification_deliveries", "webhook_deliveries", "user_notifications",
    "container_repositories", "container_images", "container_tags", "registry_events",
}
KNOWN_TYPES = (sa.Uuid, sa.DateTime, sa.LargeBinary, sa.String, sa.Integer, sa.Boolean,
               sa.JSON, sa.ARRAY)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


def _spec(name):
    return next(spec for spec in TABLES if spec.name == name)


def test_every_table_is_either_configuration_or_history():
    config = {spec.name for spec in TABLES}
    assert config | HISTORY_TABLES == set(Base.metadata.tables)
    assert not config & HISTORY_TABLES


def test_parents_come_before_the_tables_that_point_at_them():
    position = {spec.name: index for index, spec in enumerate(TABLES)}
    for spec in TABLES:
        for fk in spec.table.foreign_keys:
            parent = fk.column.table.name
            if parent in position and parent != spec.name:
                assert position[parent] < position[spec.name], f"{parent} must precede {spec.name}"


def test_every_configuration_table_has_one_key_column_and_known_column_types():
    for spec in TABLES:
        assert spec.key in spec.table.c
        for column in spec.table.columns:
            assert isinstance(column.type, KNOWN_TYPES), f"{spec.name}.{column.name}: {column.type!r}"
        for name in spec.encrypted:
            assert name in spec.table.c, f"{spec.name} has no column {name}"


def test_every_binary_column_of_the_configuration_is_declared_encrypted():
    """A new encrypted column that is not declared would be restored
    unreadable on a server with another key."""
    for spec in TABLES:
        binary = {c.name for c in spec.table.columns if isinstance(c.type, sa.LargeBinary)}
        assert binary <= set(spec.encrypted), f"{spec.name}: {binary - set(spec.encrypted)}"


def test_server_state_settings_are_recognised():
    settings = _spec("system_settings")
    assert is_server_state(settings, {"key": "backup_passphrase"})
    assert is_server_state(settings, {"key": "backup_state"})
    assert is_server_state(settings, {"key": "maintenance_mode"})
    assert is_server_state(settings, {"key": "maintenance_message"})
    assert not is_server_state(settings, {"key": "ai_model"})
    assert not is_server_state(_spec("users"), {"key": "backup_passphrase"})


async def test_export_holds_every_configuration_row_as_json(sf):
    async with sf() as db:
        ids = await seed_world(db)
        await db.commit()
    async with sf() as db:
        payload, counts = await export_configuration(db, SECRET_KEY)

    assert json.loads(json.dumps(payload)) == payload, "the payload is plain JSON"
    assert list(payload["tables"]) == [spec.name for spec in TABLES]
    assert counts["users"] == 2 and counts["projects"] == 2 and counts["pipelines"] == 1
    assert all(count >= 1 for count in counts.values()), counts
    pipeline = payload["tables"]["pipelines"][0]
    assert pipeline["id"] == str(ids["pipeline"]) and pipeline["yaml_content"].startswith("name: deploy")
    assert "builds" not in payload["tables"] and "audit_log" not in payload["tables"]


async def test_export_leaves_server_state_settings_out(sf):
    async with sf() as db:
        await seed_world(db)
        await db.commit()
    async with sf() as db:
        payload, counts = await export_configuration(db, SECRET_KEY)

    assert [row["key"] for row in payload["tables"]["system_settings"]] == ["ai_model"]
    assert counts["system_settings"] == 1


async def test_export_holds_secret_values_decrypted_and_marked(sf):
    async with sf() as db:
        await seed_world(db)
        await db.commit()
    async with sf() as db:
        payload, _ = await export_configuration(db, SECRET_KEY)
    tables = payload["tables"]

    import base64

    def plain(value):
        assert set(value) == {"$enc"}
        return base64.b64decode(value["$enc"]).decode()

    assert plain(tables["secrets"][0]["encrypted_payload"]) == "s3cr3t-value"
    assert plain(tables["git_provider_connections"][0]["encrypted_credential"]) == "ghp_token"
    assert plain(tables["project_repositories"][0]["webhook_secret_hash"]) == "webhook-secret"
    assert json.loads(plain(tables["notification_channels"][0]["config_encrypted"])) == {
        "webhook_url": "https://hooks.example/T/B/x"}
    assert tables["git_provider_connections"][0]["encrypted_refresh_token"] is None


async def test_a_value_not_encrypted_with_the_server_key_is_carried_over_as_it_is(sf):
    async with sf() as db:
        await seed_world(db)
        await db.commit()
    async with sf() as db:
        payload, _ = await export_configuration(db, SECRET_KEY)

    assert payload["tables"]["webhook_endpoints"][0]["secret_hash"] == "sha256:not-a-fernet-token"


async def test_a_row_survives_the_trip_and_is_readable_under_another_server_key(sf):
    async with sf() as db:
        await seed_world(db)
        await db.commit()
    async with sf() as db:
        payload, _ = await export_configuration(db, SECRET_KEY)
        originals = {
            spec.name: [dict(row) for row in (await db.execute(sa.select(spec.table))).mappings()]
            for spec in TABLES
        }

    other = server_fernet(OTHER_SECRET_KEY)
    for spec in TABLES:
        by_key = {row[spec.key]: row for row in originals[spec.name]}
        for stored in payload["tables"][spec.name]:
            decoded = decode_row(spec, stored, other)
            original = by_key[decoded[spec.key]]
            for column in spec.table.columns:
                if column.name in spec.encrypted and isinstance(stored[column.name], dict):
                    assert decrypt(decoded[column.name], OTHER_SECRET_KEY) == decrypt(
                        original[column.name], SECRET_KEY)
                    assert type(decoded[column.name]) is type(original[column.name])
                else:
                    assert decoded[column.name] == original[column.name], f"{spec.name}.{column.name}"


def test_encode_row_tolerates_a_string_token_and_a_missing_value():
    spec = _spec("project_repositories")
    fernet = server_fernet(SECRET_KEY)
    row = {column.name: None for column in spec.table.columns}
    row["webhook_secret_hash"] = fernet.encrypt(b"abc").decode()

    encoded = encode_row(spec, row, fernet)

    assert set(encoded["webhook_secret_hash"]) == {"$enc"}
    assert encoded["webhook_slug"] is None
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_tables.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.backup.export'`.

- [ ] **Step 3: Write the modules**

Create `backend/app/services/backup/tables.py`:

```python
"""Which tables are configuration, and how one of their rows is written into
a backup and read back.

Rows are handled as plain column-name → value mappings through the table
objects, not through the ORM classes, so a backup holds exactly the columns
the database has.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken

from app.core.security import _derive_fernet_key
from app.models.agent import Agent
from app.models.api_token import ApiToken
from app.models.git_integration import GitProviderConnection, ProjectRepository
from app.models.notification import NotificationChannel
from app.models.pipeline import Pipeline
from app.models.project import Project
from app.models.registry import RegistryDeployToken
from app.models.role import Role, UserRole
from app.models.secret import EnvVar, Secret
from app.models.system_setting import SystemSetting
from app.models.trigger import Trigger, WebhookEndpoint
from app.models.user import User


@dataclass(frozen=True)
class TableSpec:
    table: sa.Table
    # Columns the server stores encrypted with MEGOOCI_SECRET_KEY. A backup
    # holds their plain value, so a server with another key can restore them.
    encrypted: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return self.table.name

    @property
    def key(self) -> str:
        """The name of the table's single primary-key column."""
        (column,) = self.table.primary_key.columns
        return column.name


# The configuration, parents before children: rows are written in this order
# and removed in the reverse.
TABLES: tuple[TableSpec, ...] = (
    TableSpec(User.__table__),
    TableSpec(Role.__table__),
    TableSpec(UserRole.__table__),
    TableSpec(Project.__table__),
    TableSpec(
        GitProviderConnection.__table__,
        encrypted=("encrypted_credential", "encrypted_refresh_token", "encrypted_oauth_client_secret"),
    ),
    TableSpec(ProjectRepository.__table__, encrypted=("webhook_secret_hash",)),
    TableSpec(Pipeline.__table__),
    TableSpec(Trigger.__table__),
    TableSpec(WebhookEndpoint.__table__, encrypted=("secret_hash",)),
    TableSpec(Secret.__table__, encrypted=("encrypted_payload",)),
    TableSpec(EnvVar.__table__),
    TableSpec(NotificationChannel.__table__, encrypted=("config_encrypted",)),
    TableSpec(Agent.__table__),
    TableSpec(ApiToken.__table__),
    TableSpec(RegistryDeployToken.__table__),
    TableSpec(SystemSetting.__table__),
)

# Settings that describe this server rather than its configuration. They are
# never written into a backup and never touched by a restore: a restore must
# not turn maintenance mode off under the administrator's feet, or change
# where and how the next backup is made.
SERVER_STATE_PREFIX = "backup_"
SERVER_STATE_KEYS = frozenset({"maintenance_mode", "maintenance_message"})


def is_server_state(spec: TableSpec, row: Mapping[str, Any]) -> bool:
    if spec.table is not SystemSetting.__table__:
        return False
    key = str(row["key"])
    return key.startswith(SERVER_STATE_PREFIX) or key in SERVER_STATE_KEYS


def server_fernet(secret_key: str) -> Fernet:
    """The cipher the server encrypts stored secrets with."""
    return Fernet(_derive_fernet_key(secret_key))


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _b64(bytes(value))
    return value


def _decode(column: sa.Column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, sa.Uuid):
        return uuid.UUID(value)
    if isinstance(column.type, sa.DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, sa.LargeBinary):
        return base64.b64decode(value)
    return value


def encode_row(spec: TableSpec, row: Mapping[str, Any], fernet: Fernet) -> dict[str, Any]:
    """A database row as JSON-ready values. Values encrypted with the server
    key are decrypted and marked, so they can be encrypted again on restore."""
    out: dict[str, Any] = {}
    for column in spec.table.columns:
        value = row[column.name]
        if value is not None and column.name in spec.encrypted:
            token = bytes(value) if not isinstance(value, str) else value.encode()
            try:
                out[column.name] = {"$enc": _b64(fernet.decrypt(token))}
                continue
            except InvalidToken:
                # Not written with this server's key (a hash, or a leftover
                # from an old key): carried over exactly as it is.
                pass
        out[column.name] = _encode(value)
    return out


def decode_row(spec: TableSpec, data: Mapping[str, Any], fernet: Fernet) -> dict[str, Any]:
    """The inverse of ``encode_row``: values ready to be written to the
    database, with marked values encrypted with this server's key."""
    out: dict[str, Any] = {}
    for column in spec.table.columns:
        if column.name not in data:
            continue
        value = data[column.name]
        if (
            column.name in spec.encrypted
            and isinstance(value, dict)
            and set(value) == {"$enc"}
        ):
            token = fernet.encrypt(base64.b64decode(value["$enc"]))
            out[column.name] = token if isinstance(column.type, sa.LargeBinary) else token.decode()
        else:
            out[column.name] = _decode(column, value)
    return out
```

Create `backend/app/services/backup/export.py`:

```python
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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_tables.py
```

Expected: `11 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backup/tables.py backend/app/services/backup/export.py backend/tests/_backup.py backend/tests/test_backup_tables.py
git commit -m "feat(backup): export the configuration tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: The restore

**Files:**
- Create: `backend/app/services/backup/restore.py`
- Test: `backend/tests/test_backup_restore.py` (new)

**Interfaces:**
- Consumes: `TABLES`, `TableSpec`, `decode_row`, `is_server_state`, `server_fernet` (Task 2).
- Produces: `async apply_configuration(db, payload, secret_key, *, acting_user_id) -> dict[str, dict[str, int]]` — per table `{"written": n, "removed": m}`. Runs in the caller's transaction and never commits.

**How it works** (the order matters, and the tests run with foreign keys enforced):

1. Decode the backup's rows; apply the rule for the acting administrator; mark agents offline; cancel pending builds.
2. For each table, the rows to remove are those in the database and not in the backup (never server-state settings).
3. Tables nothing else in the configuration points at lose those rows now. The others keep them for the moment, because rows that are about to be rewritten still point at them; they only give up their unique values (a name, an email) so a row from the backup can take them.
4. Write the backup's rows, parents first; a sub-project after its parent.
5. Remove the remaining rows, children first.
6. Before any row is removed, history that points at it and has no database rule of its own is handled: a reference that may be empty is cleared; a row whose reference may not be empty is removed.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backup_restore.py`:

```python
"""Restoring makes the configuration equal to the backup's, keeps the history
of what still exists, and changes nothing when it cannot finish."""
import copy
import os
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.services.backup.export import export_configuration
from app.services.backup.restore import apply_configuration
from app.services.backup.tables import TABLES
from tests._backup import (
    OTHER_SECRET_KEY,
    SECRET_KEY,
    decrypt,
    encrypt,
    insert,
    rows,
    seed_world,
    snapshot,
)
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def other_server():
    """A second, empty server with another secret key."""
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _seed(sf):
    async with sf() as db:
        ids = await seed_world(db)
        await db.commit()
    return ids


async def _export(sf, key=SECRET_KEY):
    async with sf() as db:
        payload, _ = await export_configuration(db, key)
    return payload


async def _restore(sf, payload, acting, key=SECRET_KEY):
    async with sf() as db:
        summary = await apply_configuration(db, payload, key, acting_user_id=acting)
        await db.commit()
    return summary


async def _comparable(sf, key=SECRET_KEY):
    """The configuration with encrypted values shown in the clear, so two
    servers with different keys can be compared."""
    async with sf() as db:
        tables = await snapshot(db)
    for spec in TABLES:
        for row in tables[spec.name]:
            for name in spec.encrypted:
                if row[name] is not None:
                    try:
                        row[name] = decrypt(row[name], key)
                    except Exception:
                        pass  # was never encrypted with the server key
            if spec.name == "agents":
                row.update(status="offline", connected_at=None, current_build_id=None)
    return tables


async def _count(sf, model):
    async with sf() as db:
        return len(await rows(db, model))


# ── the configuration afterwards ────────────────────────────────────────

async def test_restoring_a_servers_own_backup_changes_nothing(sf):
    ids = await _seed(sf)
    before = await _comparable(sf)

    summary = await _restore(sf, await _export(sf), ids["admin"])

    assert await _comparable(sf) == before
    assert summary["users"] == {"written": 2, "removed": 0}
    assert summary["system_settings"] == {"written": 1, "removed": 0}


async def test_restoring_onto_an_empty_server_with_another_key(sf, other_server):
    from app.models.user import User

    await _seed(sf)
    payload = await _export(sf)
    async with other_server() as db:
        bootstrap = await insert(db, User, email="bootstrap@example.com", name="Bootstrap",
                                 hashed_password="hash-bootstrap", is_admin=True, is_active=True)
        await db.commit()

    await _restore(other_server, payload, bootstrap, key=OTHER_SECRET_KEY)

    expected = await _comparable(sf)
    restored = await _comparable(other_server, key=OTHER_SECRET_KEY)
    kept = [row for row in restored["users"] if row["email"] == "bootstrap@example.com"]
    restored["users"] = [row for row in restored["users"] if row["email"] != "bootstrap@example.com"]
    expected["system_settings"] = [row for row in expected["system_settings"]
                                   if row["key"] == "ai_model"]
    assert restored == expected
    assert len(kept) == 1 and kept[0]["is_admin"] is True, "the restoring admin is kept"


async def test_secrets_are_readable_with_the_new_servers_key_only(sf, other_server):
    from app.models.git_integration import GitProviderConnection
    from app.models.secret import Secret
    from app.models.user import User

    await _seed(sf)
    payload = await _export(sf)
    async with other_server() as db:
        bootstrap = await insert(db, User, email="b@example.com", is_admin=True)
        await db.commit()

    await _restore(other_server, payload, bootstrap, key=OTHER_SECRET_KEY)

    async with other_server() as db:
        secret = (await rows(db, Secret))[0]
        connection = (await rows(db, GitProviderConnection))[0]
    assert decrypt(secret["encrypted_payload"], OTHER_SECRET_KEY) == "s3cr3t-value"
    assert decrypt(connection["encrypted_credential"], OTHER_SECRET_KEY) == "ghp_token"
    with pytest.raises(Exception):
        decrypt(secret["encrypted_payload"], SECRET_KEY)


async def test_sub_projects_are_written_after_their_parents_whatever_the_order(sf, other_server):
    from app.models.project import Project
    from app.models.user import User

    ids = await _seed(sf)
    payload = await _export(sf)
    payload["tables"]["projects"].sort(key=lambda row: row["parent_id"] is None)  # child first
    assert payload["tables"]["projects"][0]["id"] == str(ids["project"])
    async with other_server() as db:
        bootstrap = await insert(db, User, email="b@example.com", is_admin=True)
        await db.commit()

    await _restore(other_server, payload, bootstrap, key=OTHER_SECRET_KEY)

    async with other_server() as db:
        projects = {row["name"]: row for row in await rows(db, Project)}
    assert projects["Web"]["parent_id"] == projects["Platform"]["id"]


# ── a server that changed since the backup ──────────────────────────────

async def _change_everything(sf, ids):
    """What an administrator might do in the days after a backup."""
    from app.models.agent import Agent
    from app.models.audit import AuditLogEntry
    from app.models.build import Build, LogChunk, Stage, Step
    from app.models.notification import NotificationChannel, NotificationDelivery
    from app.models.pipeline import Pipeline
    from app.models.project import Project
    from app.models.registry import ContainerRepository
    from app.models.secret import Secret
    from app.models.user import User

    new = {}
    async with sf() as db:
        new["user"] = await insert(db, User, email="newcomer@example.com", is_admin=False)
        new["project"] = await insert(db, Project, name="Mobile", slug="mobile",
                                      created_by=new["user"], parent_id=ids["project"])
        new["pipeline"] = await insert(db, Pipeline, name="mobile-ci", project_id=new["project"],
                                       created_by=new["user"])
        new["build"] = await insert(db, Build, pipeline_id=new["pipeline"], number=1,
                                    status="success", triggered_by=new["user"])
        stage = await insert(db, Stage, build_id=new["build"], name="s", sort_order=0)
        step = await insert(db, Step, stage_id=stage, name="t", sort_order=0)
        await insert(db, LogChunk, step_id=step, seq=1, content="x")
        new["registry"] = await insert(db, ContainerRepository, project_id=new["project"], name="app")
        # A build of a pipeline that survives, started by a user who will not.
        new["kept_build"] = await insert(db, Build, pipeline_id=ids["pipeline"], number=2,
                                         status="failed", triggered_by=new["user"])
        new["audit"] = await insert(db, AuditLogEntry, action="project.create",
                                    target_type="project", actor_id=new["user"])
        new["channel"] = await insert(db, NotificationChannel, name="new-chat", channel_type="slack",
                                      created_by=new["user"], config_encrypted=encrypt("{}"))
        new["delivery"] = await insert(db, NotificationDelivery, channel_id=new["channel"],
                                       message="hello")
        new["agent"] = await insert(db, Agent, name="mac-1")

        await db.execute(sa.update(Pipeline.__table__).where(Pipeline.id == ids["pipeline"])
                         .values(name="deploy-renamed", yaml_content="name: changed\n"))
        await db.execute(sa.update(NotificationChannel.__table__)
                         .where(NotificationChannel.id == ids["channel"])
                         .values(enabled=False, config_encrypted=encrypt('{"webhook_url": "changed"}')))
        await db.execute(sa.delete(Secret.__table__).where(Secret.id == ids["secret"]))
        await db.commit()
    return new


async def test_a_changed_server_is_put_back_exactly(sf):
    ids = await _seed(sf)
    payload = await _export(sf)
    before = await _comparable(sf)
    await _change_everything(sf, ids)
    assert await _comparable(sf) != before

    summary = await _restore(sf, payload, ids["admin"])

    assert await _comparable(sf) == before
    assert summary["projects"]["removed"] == 1 and summary["secrets"]["written"] == 1
    assert summary["users"]["removed"] == 1 and summary["agents"]["removed"] == 1


async def test_history_of_what_survives_is_kept_and_of_what_is_removed_goes(sf):
    from app.models.audit import AuditLogEntry
    from app.models.build import Build, LogChunk
    from app.models.notification import NotificationDelivery
    from app.models.registry import ContainerRepository

    ids = await _seed(sf)
    payload = await _export(sf)
    new = await _change_everything(sf, ids)

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        builds = {row["id"]: row for row in await rows(db, Build)}
        audit = {row["id"]: row for row in await rows(db, AuditLogEntry)}
        deliveries = {row["id"] for row in await rows(db, NotificationDelivery)}
        logs = await rows(db, LogChunk)
        registry = await rows(db, ContainerRepository)

    assert ids["build"] in builds, "the surviving pipeline keeps its builds"
    assert [log["id"] for log in logs] == [ids["log"]], "and their logs"
    assert new["build"] not in builds, "the removed pipeline's builds go with it"
    assert registry == [], "and the removed project's registry repositories"
    assert builds[new["kept_build"]]["triggered_by"] is None, "the build stays, its user does not"
    assert builds[ids["build"]]["triggered_by"] == ids["dev"]
    assert audit[new["audit"]]["actor_id"] is None and audit[ids["audit"]]["actor_id"] == ids["dev"]
    assert deliveries == {ids["delivery"]}, "the removed channel's deliveries go with it"


async def test_a_name_taken_by_something_newer_goes_back_to_its_owner(sf):
    """After the backup, dev's email was changed and given to a new account,
    and the project's name to a new project. Both must come back."""
    from app.models.project import Project
    from app.models.user import User

    ids = await _seed(sf)
    payload = await _export(sf)
    async with sf() as db:
        await db.execute(sa.update(User.__table__).where(User.id == ids["dev"])
                         .values(email="dev-old@example.com"))
        await db.execute(sa.update(Project.__table__).where(Project.id == ids["project"])
                         .values(name="Web (old)", slug="web-old"))
        usurper = await insert(db, User, email="dev@example.com")
        await insert(db, Project, name="Web", slug="web", created_by=usurper)
        await db.commit()

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        users = {row["email"]: row["id"] for row in await rows(db, User)}
        projects = {row["slug"]: row["id"] for row in await rows(db, Project)}
    assert users == {"admin@example.com": ids["admin"], "dev@example.com": ids["dev"]}
    assert projects == {"platform": ids["parent_project"], "web": ids["project"]}


async def test_pending_builds_are_cancelled_and_agents_are_offline(sf):
    from app.models.agent import Agent
    from app.models.build import Build, Stage, Step

    ids = await _seed(sf)
    async with sf() as db:
        pending = await insert(db, Build, pipeline_id=ids["pipeline"], number=5, status="pending")
        stage = await insert(db, Stage, build_id=pending, name="s", sort_order=0, status="pending")
        step = await insert(db, Step, stage_id=stage, name="t", sort_order=0, status="pending")
        await db.commit()

    await _restore(sf, await _export(sf), ids["admin"])

    async with sf() as db:
        builds = {row["id"]: row for row in await rows(db, Build)}
        stages = {row["id"]: row["status"] for row in await rows(db, Stage)}
        steps = {row["id"]: row["status"] for row in await rows(db, Step)}
        agent = (await rows(db, Agent))[0]
    assert builds[pending]["status"] == "cancelled" and builds[pending]["finished_at"] is not None
    assert (stages[stage], steps[step]) == ("cancelled", "cancelled")
    assert builds[ids["build"]]["status"] == "success" and stages[ids["stage"]] == "success"
    assert (agent["status"], agent["connected_at"], agent["current_build_id"]) == ("offline", None, None)


# ── server state ────────────────────────────────────────────────────────

async def test_server_state_settings_are_never_touched(sf):
    from app.models.system_setting import SystemSetting

    ids = await _seed(sf)
    payload = await _export(sf)
    # Even a backup that carries them (made by hand, or by a later version).
    payload["tables"]["system_settings"] += [
        {"key": "maintenance_mode", "value": "false", "updated_at": "2026-01-01T00:00:00"},
        {"key": "backup_schedule", "value": "{}", "updated_at": "2026-01-01T00:00:00"},
    ]
    async with sf() as db:
        await db.execute(sa.update(SystemSetting.__table__)
                         .where(SystemSetting.key == "ai_model").values(value="changed"))
        await db.execute(sa.insert(SystemSetting.__table__).values(key="ai_provider", value="x"))
        await db.commit()

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        settings = {row["key"]: row["value"] for row in await rows(db, SystemSetting)}
    assert settings == {
        "ai_model": "gpt-test",                         # restored
        "maintenance_mode": "true",                     # left alone
        "backup_schedule": '{"frequency": "daily"}',    # left alone
    }


# ── the administrator who restores ──────────────────────────────────────

async def _users(sf):
    from app.models.user import User

    async with sf() as db:
        return {row["email"]: row for row in await rows(db, User)}


async def test_an_admin_the_backup_does_not_know_is_kept(sf):
    from app.models.user import User

    ids = await _seed(sf)
    payload = await _export(sf)
    async with sf() as db:
        later = await insert(db, User, email="later@example.com", name="Later",
                             hashed_password="hash-later", is_admin=True, is_active=True)
        await db.commit()

    await _restore(sf, payload, later)

    users = await _users(sf)
    assert set(users) == {"admin@example.com", "dev@example.com", "later@example.com"}
    kept = users["later@example.com"]
    assert (kept["id"], kept["hashed_password"], kept["is_admin"]) == (later, "hash-later", True)


async def test_an_admin_the_backup_knows_keeps_their_current_password_and_rights(sf):
    from app.models.user import User

    ids = await _seed(sf)
    payload = await _export(sf)
    for row in payload["tables"]["users"]:
        if row["id"] == str(ids["admin"]):
            row.update(hashed_password="hash-from-long-ago", is_admin=False, is_active=False,
                       name="Name In Backup")
    async with sf() as db:
        await db.execute(sa.update(User.__table__).where(User.id == ids["admin"])
                         .values(hashed_password="hash-current"))
        await db.commit()

    await _restore(sf, payload, ids["admin"])

    admin = (await _users(sf))["admin@example.com"]
    assert admin["hashed_password"] == "hash-current"
    assert (admin["is_admin"], admin["is_active"]) == (True, True)
    assert admin["name"] == "Name In Backup", "everything else comes from the backup"


async def test_an_admin_the_backup_knows_by_email_under_another_id(sf):
    """The server was rebuilt since the backup: same people, new ids."""
    from app.models.user import User

    ids = await _seed(sf)
    payload = await _export(sf)
    old_id = str(uuid.uuid4())
    payload["tables"]["users"].append({
        **copy.deepcopy(payload["tables"]["users"][0]), "id": old_id, "email": "Ops@Example.com",
        "hashed_password": "hash-from-long-ago", "is_admin": False,
    })
    async with sf() as db:
        acting = await insert(db, User, email="ops@example.com", hashed_password="hash-current",
                              is_admin=True, is_active=True)
        await db.commit()

    await _restore(sf, payload, acting)

    users = await _users(sf)
    ops = users["Ops@Example.com"]
    assert str(ops["id"]) == old_id, "the backup's account is the one that exists afterwards"
    assert (ops["hashed_password"], ops["is_admin"], ops["is_active"]) == ("hash-current", True, True)
    assert "ops@example.com" not in users


# ── all or nothing ──────────────────────────────────────────────────────

async def test_a_restore_that_fails_midway_changes_nothing(sf):
    from app.models.build import Build
    from app.models.notification import NotificationDelivery

    ids = await _seed(sf)
    payload = await _export(sf)
    new = await _change_everything(sf, ids)
    before = await _comparable(sf)
    # A row that cannot be written: its project does not exist.
    payload["tables"]["pipelines"].append({
        **copy.deepcopy(payload["tables"]["pipelines"][0]), "id": str(uuid.uuid4()),
        "name": "orphan", "project_id": str(uuid.uuid4()), "project_repository_id": None,
    })

    async with sf() as db:
        with pytest.raises(Exception):
            await apply_configuration(db, payload, SECRET_KEY, acting_user_id=ids["admin"])
        await db.rollback()

    assert await _comparable(sf) == before
    assert await _count(sf, Build) == 3 and await _count(sf, NotificationDelivery) == 2
    async with sf() as db:
        assert {row["id"] for row in await rows(db, Build)} >= {new["build"], new["kept_build"]}


async def test_a_backup_from_another_key_that_was_not_reencrypted_is_still_applied(sf):
    """Values the exporting server could not decrypt are carried over as they
    are; the restore must not choke on them."""
    from app.models.trigger import WebhookEndpoint

    ids = await _seed(sf)
    payload = await _export(sf)

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        assert (await rows(db, WebhookEndpoint))[0]["secret_hash"] == "sha256:not-a-fernet-token"


async def test_a_backup_without_a_table_empties_it(sf):
    """An older backup made before a table held anything."""
    from app.models.secret import EnvVar

    ids = await _seed(sf)
    payload = await _export(sf)
    del payload["tables"]["env_vars"]

    summary = await _restore(sf, payload, ids["admin"])

    assert await _count(sf, EnvVar) == 0 and summary["env_vars"] == {"written": 0, "removed": 1}
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_restore.py
```

Expected: a collection error, `ModuleNotFoundError: No module named 'app.services.backup.restore'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/backup/restore.py`:

```python
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


async def _release_unique_values(db: AsyncSession, spec: TableSpec, keys: list[Any]) -> None:
    """Rows that will be removed last must not hold a name or an email a row
    from the backup needs in the meantime."""
    columns = [
        column for column in spec.table.columns
        if column.unique and isinstance(column.type, sa.String) and not column.primary_key
    ]
    if not columns:
        return
    key = spec.table.c[spec.key]
    for value in keys:
        await db.execute(
            sa.update(spec.table)
            .where(key == value)
            .values({column.name: f"~removed~{uuid.uuid4().hex}" for column in columns})
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
    for agent in rows[Agent.__tablename__]:
        # Agents reconnect on their own; until then none is connected.
        agent.update(status="offline", current_build_id=None, connected_at=None)
    await _cancel_pending_builds(db)

    existing: dict[str, set[Any]] = {}
    doomed: dict[str, list[Any]] = {}
    for spec in TABLES:
        current = (await db.execute(sa.select(spec.table))).mappings().all()
        keep = {row[spec.key] for row in rows[spec.name]}
        existing[spec.name] = {row[spec.key] for row in current}
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
            await _release_unique_values(db, spec, doomed[spec.name])

    # 2. Write the backup's rows, parents first.
    for spec in TABLES:
        key = spec.table.c[spec.key]
        for row in _parents_first(spec, rows[spec.name]):
            if row[spec.key] in existing[spec.name]:
                values = {name: value for name, value in row.items() if name != spec.key}
                if values:
                    await db.execute(
                        sa.update(spec.table).where(key == row[spec.key]).values(values)
                    )
            else:
                await db.execute(sa.insert(spec.table).values(row))

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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_restore.py
```

Expected: `15 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backup/restore.py backend/tests/test_backup_restore.py
git commit -m "feat(backup): restore the configuration in one transaction

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: The backups directory

**Files:**
- Create: `backend/app/services/backup/store.py`
- Test: `backend/tests/test_backup_store.py` (new)

**Interfaces:**
- Consumes: `KINDS`, `Header`, `read_header`, `BackupFormatError` (Task 1).
- Produces: `InvalidBackupName`, `BackupNotFound`, `Busy` (exceptions); `LOCK_STALE_SECONDS`.
- Produces: `BackupFile(name, kind, created_at, size, header, error)`.
- Produces: `backups_dir() -> Path` (`<MEGOOCI_STORAGE_ROOT>/backups`, created on demand). Tests replace this function to point at a temporary directory.
- Produces: `parse_name(name) -> (kind, datetime)`, `path_of(name)`, `new_name(kind, when)`, `write(name, data)`, `read(name)`, `delete(name)`, `describe(name) -> BackupFile`, `list_backups() -> list[BackupFile]` (newest first).
- Produces: `exclusive()` — a context manager holding the one-at-a-time lock; raises `Busy`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backup_store.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_store.py
```

Expected: a collection error, `ImportError: cannot import name 'store' from 'app.services.backup'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/backup/store.py`:

```python
"""The backups directory.

The directory is the list of backups: nothing about a backup is kept in the
database, so the list survives losing it, and a file copied in by hand
appears like any other.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings
from app.services.backup.format import KINDS, BackupFormatError, Header, read_header

SUFFIX = ".mcbak"
_NAME = re.compile(
    r"^megooci-backup-(\d{8})-(\d{6})-(" + "|".join(KINDS) + r")(?:-(\d{1,3}))?\.mcbak\Z"
)
_LOCK_NAME = ".lock"
# A lock older than this was left by a process that died.
LOCK_STALE_SECONDS = 30 * 60


class InvalidBackupName(Exception):
    """Not a name this server gives its backup files."""


class BackupNotFound(Exception):
    pass


class Busy(Exception):
    """Another backup or restore is running."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    kind: str
    created_at: datetime
    size: int
    # None when the file's header cannot be read; ``error`` then says why.
    header: Header | None
    error: str | None = None


def backups_dir() -> Path:
    path = Path(get_settings().MEGOOCI_STORAGE_ROOT) / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def parse_name(name: str) -> tuple[str, datetime]:
    """The kind and the time in a backup file name. Raises for any other
    name, so a name from a request can never reach outside the directory."""
    match = _NAME.match(name)
    if match is None:
        raise InvalidBackupName(name)
    try:
        when = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError as exc:
        raise InvalidBackupName(name) from exc
    return match.group(3), when.replace(tzinfo=timezone.utc)


def path_of(name: str) -> Path:
    parse_name(name)
    return backups_dir() / name


def new_name(kind: str, when: datetime) -> str:
    """A name for a backup of *kind* made at *when* that is not taken yet."""
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"megooci-backup-{stamp}-{kind}"
    name = f"{base}{SUFFIX}"
    number = 2
    while (backups_dir() / name).exists():
        name = f"{base}-{number}{SUFFIX}"
        number += 1
    parse_name(name)
    return name


def write(name: str, data: bytes) -> None:
    """Write a backup file. It appears under its name only when complete."""
    path = path_of(name)
    partial = path.with_name(path.name + ".part")
    try:
        partial.write_bytes(data)
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def read(name: str) -> bytes:
    path = path_of(name)
    if not path.is_file():
        raise BackupNotFound(name)
    return path.read_bytes()


def delete(name: str) -> None:
    path = path_of(name)
    if not path.is_file():
        raise BackupNotFound(name)
    path.unlink()


def describe(name: str) -> BackupFile:
    path = path_of(name)
    if not path.is_file():
        raise BackupNotFound(name)
    kind, created_at = parse_name(name)
    header: Header | None = None
    error: str | None = None
    try:
        header = read_header(path.read_bytes())
    except BackupFormatError as exc:
        error = str(exc)
    return BackupFile(name=name, kind=kind, created_at=created_at, size=path.stat().st_size,
                      header=header, error=error)


def list_backups() -> list[BackupFile]:
    """Every backup in the directory, newest first."""
    found = []
    for path in backups_dir().iterdir():
        if _NAME.match(path.name) and path.is_file():
            found.append(describe(path.name))
    return sorted(found, key=lambda backup: (backup.created_at, backup.name), reverse=True)


@contextmanager
def exclusive() -> Iterator[None]:
    """Hold the one-at-a-time lock for backups and restores. Raises ``Busy``
    when another one holds it. The lock is a file, because the web server and
    the scheduled-task worker are different processes sharing this directory."""
    lock = backups_dir() / _LOCK_NAME
    try:
        if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise Busy("Another backup or restore is running.") from exc
    try:
        os.close(descriptor)
        yield
    finally:
        lock.unlink(missing_ok=True)
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_store.py
```

Expected: `22 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backup/store.py backend/tests/test_backup_store.py
git commit -m "feat(backup): backups directory with strict file names and a lock

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Schedule and settings

**Files:**
- Create: `backend/app/services/backup/schedule.py`, `backend/app/services/backup/settings.py`
- Test: `backend/tests/test_backup_schedule.py` (new)

**Interfaces:**
- Consumes: `server_fernet` (Task 2); `tests/_backup.py`.
- Produces, in `schedule.py`: `Schedule(frequency="off", time="03:00", weekday=0, keep=14)` with `validate()`; `InvalidSchedule(ValueError)`; `latest_slot(schedule, now) -> datetime | None`; `is_due(schedule, now, *, since) -> bool`; `to_prune(names_newest_first, keep) -> list[str]`.
- Produces, in `settings.py`: keys `backup_passphrase`, `backup_schedule`, `backup_remote`, `backup_state`; `MIN_PASSPHRASE_LENGTH = 12`; `InvalidSettings(ValueError)`; `RemoteConfig(enabled, endpoint_url, region, bucket, prefix, access_key_id, secret_access_key)` with `validate()` and `object_key(name)`.
- Produces, in `settings.py` (none of these commit): `get_passphrase(db, secret_key)`, `set_passphrase(db, secret_key, passphrase)`, `get_schedule(db)`, `set_schedule(db, schedule, now)`, `get_remote(db, secret_key)`, `set_remote(db, secret_key, config)`, `get_state(db) -> dict`, `save_state(db, state)`.
- The state dict: `schedule_saved_at` (ISO time), `last_run` (`at`, `ok`, `error`, `name`), `remote` (file name → `status`, `error`, `at`).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backup_schedule.py`:

```python
"""When a scheduled backup is due, which old ones go, and the settings."""
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.services.backup import settings
from app.services.backup.schedule import InvalidSchedule, Schedule, is_due, latest_slot, to_prune
from app.services.backup.settings import InvalidSettings, RemoteConfig
from tests._backup import OTHER_SECRET_KEY, SECRET_KEY, rows
from tests._rbac import build_inmemory_factory


def at(day, hour=0, minute=0):
    """October 2026; the 5th is a Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


DAILY = Schedule(frequency="daily", time="03:00")
WEEKLY = Schedule(frequency="weekly", time="03:00", weekday=2)  # Wednesday


# ── the slot ────────────────────────────────────────────────────────────

def test_off_is_never_due():
    assert latest_slot(Schedule(), at(8, 12)) is None
    assert is_due(Schedule(), at(8, 12), since=None) is False


def test_daily_slot_is_today_once_the_time_has_passed_else_yesterday():
    assert latest_slot(DAILY, at(8, 3, 0)) == at(8, 3)
    assert latest_slot(DAILY, at(8, 23, 59)) == at(8, 3)
    assert latest_slot(DAILY, at(8, 2, 59)) == at(7, 3)


def test_weekly_slot_is_the_latest_such_weekday():
    assert latest_slot(WEEKLY, at(7, 3, 0)) == at(7, 3)      # Wednesday the 7th
    assert latest_slot(WEEKLY, at(9, 12)) == at(7, 3)
    assert latest_slot(WEEKLY, at(7, 2, 59)) == at(7, 3) - timedelta(days=7)
    assert latest_slot(WEEKLY, at(13, 23)) == at(7, 3)       # Tuesday: still last week's
    assert latest_slot(WEEKLY, at(14, 3)) == at(14, 3)


def test_the_slot_is_in_utc_whatever_zone_now_is_in():
    plus_two = timezone(timedelta(hours=2))
    now = datetime(2026, 10, 8, 4, 30, tzinfo=plus_two)  # 02:30 UTC
    assert latest_slot(DAILY, now) == at(7, 3)


# ── due ─────────────────────────────────────────────────────────────────

def test_due_when_a_slot_has_passed_since_the_last_run():
    assert is_due(DAILY, at(8, 3, 4), since=at(7, 3, 2)) is True
    assert is_due(DAILY, at(8, 3, 9), since=at(8, 3, 4)) is False
    assert is_due(DAILY, at(8, 2, 59), since=at(7, 3, 2)) is False


def test_turning_the_schedule_on_does_not_make_a_backup_at_once():
    saved = at(8, 10)
    assert is_due(DAILY, at(8, 10, 5), since=saved) is False
    assert is_due(DAILY, at(9, 3, 0), since=saved) is True


def test_a_server_that_was_down_for_days_makes_one_backup_not_several():
    assert is_due(DAILY, at(12, 9), since=at(7, 3, 1)) is True
    assert is_due(DAILY, at(12, 9, 5), since=at(12, 9)) is False


def test_never_run_and_never_saved_is_due():
    assert is_due(DAILY, at(8, 12), since=None) is True


@pytest.mark.parametrize("schedule, message", [
    (Schedule(frequency="hourly"), "Frequency must be one of"),
    (Schedule(frequency="daily", time="3:00"), "Time must be HH:MM"),
    (Schedule(frequency="daily", time="25:00"), "Time must be HH:MM"),
    (Schedule(frequency="daily", time="03:60"), "Time must be HH:MM"),
    (Schedule(frequency="daily", time="aa:bb"), "Time must be HH:MM"),
    (Schedule(frequency="weekly", weekday=7), "Weekday must be"),
    (Schedule(frequency="daily", keep=0), "Keep must be between 1 and 365"),
    (Schedule(frequency="daily", keep=1000), "Keep must be between 1 and 365"),
])
def test_invalid_schedules_say_what_is_wrong(schedule, message):
    with pytest.raises(InvalidSchedule) as exc:
        schedule.validate()
    assert message in str(exc.value)


def test_prune_keeps_the_newest():
    names = ["d5", "d4", "d3", "d2", "d1"]
    assert to_prune(names, 3) == ["d2", "d1"]
    assert to_prune(names, 5) == [] and to_prune(names, 9) == []
    assert to_prune(names, 0) == ["d4", "d3", "d2", "d1"], "never fewer than one"


# ── settings ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _stored(sf):
    from app.models.system_setting import SystemSetting

    async with sf() as db:
        return {row["key"]: row["value"] for row in await rows(db, SystemSetting, SystemSetting.key)}


async def test_the_passphrase_is_stored_encrypted_and_read_back(sf):
    async with sf() as db:
        assert await settings.get_passphrase(db, SECRET_KEY) is None
        await settings.set_passphrase(db, SECRET_KEY, "correct horse battery")
        await db.commit()

    stored = await _stored(sf)
    assert "correct horse" not in stored["backup_passphrase"]
    async with sf() as db:
        assert await settings.get_passphrase(db, SECRET_KEY) == "correct horse battery"
        assert await settings.get_passphrase(db, OTHER_SECRET_KEY) is None, "unreadable = not set"


async def test_a_short_passphrase_is_refused(sf):
    async with sf() as db:
        with pytest.raises(InvalidSettings) as exc:
            await settings.set_passphrase(db, SECRET_KEY, "too short")
    assert "at least 12 characters" in str(exc.value)


async def test_schedule_defaults_round_trip_and_when_it_was_saved(sf):
    async with sf() as db:
        assert await settings.get_schedule(db) == Schedule()
        await settings.set_schedule(db, WEEKLY, at(8, 10))
        await db.commit()
    async with sf() as db:
        assert await settings.get_schedule(db) == WEEKLY
        assert (await settings.get_state(db))["schedule_saved_at"] == at(8, 10).isoformat()
        with pytest.raises(InvalidSchedule):
            await settings.set_schedule(db, Schedule(frequency="hourly"), at(8, 10))


async def test_unreadable_stored_settings_fall_back_to_defaults(sf):
    from app.models.system_setting import SystemSetting

    async with sf() as db:
        db.add_all([SystemSetting(key="backup_schedule", value="{not json"),
                    SystemSetting(key="backup_state", value="[1, 2]"),
                    SystemSetting(key="backup_remote", value='{"enabled": true, "bucket": "b"}')])
        await db.commit()
    async with sf() as db:
        assert await settings.get_schedule(db) == Schedule()
        assert await settings.get_state(db) == {"remote": {}}
        remote = await settings.get_remote(db, SECRET_KEY)
    assert remote.enabled is True and remote.bucket == "b" and remote.secret_access_key == ""


async def test_the_remote_secret_is_stored_encrypted(sf):
    config = RemoteConfig(enabled=True, endpoint_url="https://s3.example", region="eu", bucket="b",
                          prefix="ci/", access_key_id="AKIA", secret_access_key="very-secret")
    async with sf() as db:
        await settings.set_remote(db, SECRET_KEY, config)
        await db.commit()

    assert "very-secret" not in (await _stored(sf))["backup_remote"]
    async with sf() as db:
        assert await settings.get_remote(db, SECRET_KEY) == config


@pytest.mark.parametrize("config, message", [
    (RemoteConfig(enabled=True, access_key_id="a", secret_access_key="s"), "needs a bucket"),
    (RemoteConfig(enabled=True, bucket="b", secret_access_key="s"), "needs an access key id"),
    (RemoteConfig(enabled=True, bucket="b", access_key_id="a"), "needs a secret access key"),
    (RemoteConfig(endpoint_url="s3.example"), "must start with http"),
])
def test_remote_settings_that_cannot_work_are_refused(config, message):
    with pytest.raises(InvalidSettings) as exc:
        config.validate()
    assert message in str(exc.value)


def test_turned_off_remote_settings_may_be_incomplete_and_keys_get_the_prefix():
    RemoteConfig(enabled=False, bucket="").validate()
    assert RemoteConfig(prefix="/ci/backups/").object_key("x.mcbak") == "ci/backups/x.mcbak"
    assert RemoteConfig().object_key("x.mcbak") == "x.mcbak"
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_schedule.py
```

Expected: a collection error, `ImportError: cannot import name 'settings' from 'app.services.backup'`.

- [ ] **Step 3: Write the modules**

Create `backend/app/services/backup/schedule.py`:

```python
"""When a scheduled backup is due, and which old ones to delete.

Pure functions: the scheduled task supplies the clock and the state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FREQUENCIES = ("off", "daily", "weekly")
DEFAULT_KEEP = 14


class InvalidSchedule(ValueError):
    """The message is for the user."""


@dataclass(frozen=True)
class Schedule:
    frequency: str = "off"
    time: str = "03:00"  # HH:MM, UTC
    weekday: int = 0  # 0 = Monday; used by "weekly"
    keep: int = DEFAULT_KEEP

    def validate(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise InvalidSchedule(f"Frequency must be one of: {', '.join(FREQUENCIES)}.")
        try:
            hour, minute = _clock(self.time)
        except ValueError as exc:
            raise InvalidSchedule("Time must be HH:MM, for example 03:00.") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise InvalidSchedule("Time must be HH:MM, for example 03:00.")
        if not 0 <= self.weekday <= 6:
            raise InvalidSchedule("Weekday must be 0 (Monday) to 6 (Sunday).")
        if not 1 <= self.keep <= 365:
            raise InvalidSchedule("Keep must be between 1 and 365 backups.")


def _clock(text: str) -> tuple[int, int]:
    hour, _, minute = text.partition(":")
    if len(hour) != 2 or len(minute) != 2:
        raise ValueError(text)
    return int(hour), int(minute)


def latest_slot(schedule: Schedule, now: datetime) -> datetime | None:
    """The most recent moment, not after *now*, at which the schedule calls
    for a backup. None when the schedule is off."""
    if schedule.frequency == "off":
        return None
    now = now.astimezone(timezone.utc)
    hour, minute = _clock(schedule.time)
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule.frequency == "weekly":
        slot -= timedelta(days=(slot.weekday() - schedule.weekday) % 7)
        step = timedelta(days=7)
    else:
        step = timedelta(days=1)
    if slot > now:
        slot -= step
    return slot


def is_due(schedule: Schedule, now: datetime, *, since: datetime | None) -> bool:
    """Whether a scheduled backup should be made now.

    *since* is the later of the last scheduled run and the moment the schedule
    was last saved. A backup is due when a slot has passed after it: so
    turning the schedule on does not make a backup at once, and a server that
    was down over several slots makes one backup when it returns, not several.
    """
    slot = latest_slot(schedule, now)
    if slot is None:
        return False
    return since is None or slot > since.astimezone(timezone.utc)


def to_prune(scheduled_names_newest_first: list[str], keep: int) -> list[str]:
    """The scheduled backups beyond the newest *keep*."""
    return scheduled_names_newest_first[max(keep, 1):]
```

Create `backend/app/services/backup/settings.py`:

```python
"""Backup settings and run state, kept in the system settings table.

Every key starts with ``backup_``: that prefix is what keeps these rows out of
backups and safe from restores (see ``tables.is_server_state``). Nothing here
commits; the caller does.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from cryptography.fernet import InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_setting import SystemSetting
from app.services.backup.schedule import Schedule
from app.services.backup.tables import server_fernet

KEY_PASSPHRASE = "backup_passphrase"
KEY_SCHEDULE = "backup_schedule"
KEY_REMOTE = "backup_remote"
KEY_STATE = "backup_state"

MIN_PASSPHRASE_LENGTH = 12


class InvalidSettings(ValueError):
    """The message is for the user."""


@dataclass(frozen=True)
class RemoteConfig:
    enabled: bool = False
    endpoint_url: str = ""  # empty for AWS
    region: str = ""
    bucket: str = ""
    prefix: str = ""
    access_key_id: str = ""
    secret_access_key: str = ""  # in the clear only in memory

    def validate(self) -> None:
        if self.endpoint_url and not self.endpoint_url.startswith(("http://", "https://")):
            raise InvalidSettings("The endpoint URL must start with http:// or https://.")
        if not self.enabled:
            return
        for label, value in (("a bucket", self.bucket), ("an access key id", self.access_key_id),
                             ("a secret access key", self.secret_access_key)):
            if not value.strip():
                raise InvalidSettings(f"The remote copy needs {label}.")

    def object_key(self, name: str) -> str:
        prefix = self.prefix.strip("/")
        return f"{prefix}/{name}" if prefix else name


async def _get(db: AsyncSession, key: str) -> str | None:
    row = await db.get(SystemSetting, key)
    return row.value if row is not None else None


async def _set(db: AsyncSession, key: str, value: str) -> None:
    row = await db.get(SystemSetting, key)
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    await db.flush()


def _json(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text) if text else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _decrypt(token: str | None, secret_key: str) -> str | None:
    if not token:
        return None
    try:
        return server_fernet(secret_key).decrypt(token.encode()).decode()
    except InvalidToken:
        # Written under another MEGOOCI_SECRET_KEY: as good as not set.
        return None


def _encrypt(value: str, secret_key: str) -> str:
    return server_fernet(secret_key).encrypt(value.encode()).decode()


# ── passphrase ──────────────────────────────────────────────────────────

async def get_passphrase(db: AsyncSession, secret_key: str) -> str | None:
    return _decrypt(await _get(db, KEY_PASSPHRASE), secret_key)


async def set_passphrase(db: AsyncSession, secret_key: str, passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise InvalidSettings(
            f"The passphrase must be at least {MIN_PASSPHRASE_LENGTH} characters long."
        )
    await _set(db, KEY_PASSPHRASE, _encrypt(passphrase, secret_key))


# ── schedule ────────────────────────────────────────────────────────────

async def get_schedule(db: AsyncSession) -> Schedule:
    stored = _json(await _get(db, KEY_SCHEDULE))
    defaults = Schedule()
    try:
        schedule = Schedule(
            frequency=str(stored.get("frequency", defaults.frequency)),
            time=str(stored.get("time", defaults.time)),
            weekday=int(stored.get("weekday", defaults.weekday)),
            keep=int(stored.get("keep", defaults.keep)),
        )
        schedule.validate()
    except ValueError:
        return defaults
    return schedule


async def set_schedule(db: AsyncSession, schedule: Schedule, now: datetime) -> None:
    """Save the schedule, and when it was saved: the first scheduled backup
    is made at the next slot after this moment, not at once."""
    schedule.validate()
    await _set(db, KEY_SCHEDULE, json.dumps(asdict(schedule)))
    state = await get_state(db)
    state["schedule_saved_at"] = now.isoformat()
    await save_state(db, state)


# ── remote storage ──────────────────────────────────────────────────────

async def get_remote(db: AsyncSession, secret_key: str) -> RemoteConfig:
    stored = _json(await _get(db, KEY_REMOTE))
    return RemoteConfig(
        enabled=bool(stored.get("enabled", False)),
        endpoint_url=str(stored.get("endpoint_url", "")),
        region=str(stored.get("region", "")),
        bucket=str(stored.get("bucket", "")),
        prefix=str(stored.get("prefix", "")),
        access_key_id=str(stored.get("access_key_id", "")),
        secret_access_key=_decrypt(stored.get("secret_access_key"), secret_key) or "",
    )


async def set_remote(db: AsyncSession, secret_key: str, config: RemoteConfig) -> None:
    config.validate()
    stored = asdict(config)
    stored["secret_access_key"] = (
        _encrypt(config.secret_access_key, secret_key) if config.secret_access_key else ""
    )
    await _set(db, KEY_REMOTE, json.dumps(stored))


# ── run state ───────────────────────────────────────────────────────────

async def get_state(db: AsyncSession) -> dict[str, Any]:
    """``schedule_saved_at``, ``last_run`` (at, ok, error, name) and
    ``remote`` (file name → status, error, at)."""
    state = _json(await _get(db, KEY_STATE))
    if not isinstance(state.get("remote"), dict):
        state["remote"] = {}
    return state


async def save_state(db: AsyncSession, state: dict[str, Any]) -> None:
    await _set(db, KEY_STATE, json.dumps(state))
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_schedule.py
```

Expected: `27 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backup/schedule.py backend/app/services/backup/settings.py backend/tests/test_backup_schedule.py
git commit -m "feat(backup): schedule rule and backup settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: The remote copy

**Files:**
- Modify: `backend/pyproject.toml` (one dependency)
- Create: `backend/app/services/backup/remote.py`
- Test: `backend/tests/test_backup_remote.py` (new)

**Interfaces:**
- Consumes: `RemoteConfig` (Task 5).
- Produces: `RemoteError(Exception)` — its message is the storage's own reason, at most 300 characters.
- Produces: `async upload(config, name, data: bytes)`, `async delete(config, name)`, `async check_connection(config)` (writes and deletes a small object). Each raises `RemoteError`.
- Produces: `CONNECT_TIMEOUT_SECONDS`, `READ_TIMEOUT_SECONDS`, `MAX_ATTEMPTS` (module constants the tests lower).
- `boto3` is imported inside `_client`, not at module level: it is slow to load and most servers never use it.

The tests run the real `boto3` client against a small S3 stand-in on localhost; nothing leaves the machine.

- [ ] **Step 1: Add the dependency and install it**

**`backend/pyproject.toml`**. Replace:

````
    "cryptography",
````

with:

````
    "cryptography",
    "boto3",
````

Then, from `backend/`:

```bash
./.venv/Scripts/python.exe -m pip install boto3
```

Expected: `Successfully installed boto3-… botocore-… jmespath-… s3transfer-…` (or "already satisfied").

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/test_backup_remote.py`:

```python
"""The remote copy, against a small S3 stand-in on localhost."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.services.backup import remote
from app.services.backup.remote import RemoteError
from app.services.backup.settings import RemoteConfig

NAME = "megooci-backup-20261008-030000-manual.mcbak"


class FakeS3(BaseHTTPRequestHandler):
    """Path-style S3: PUT, GET and DELETE of /bucket/key. Knows one bucket."""

    objects: dict[str, bytes] = {}
    requests: list[tuple[str, str]] = []

    def log_message(self, *args):
        pass

    def _answer(self, status, body=b""):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if body:
            self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        type(self).requests.append((self.command, self.path))
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if "authorization" not in {key.lower() for key in self.headers}:
            return self._answer(403, b"<Error><Code>AccessDenied</Code><Message>unsigned</Message></Error>")
        bucket, _, key = self.path.lstrip("/").partition("/")
        key = key.split("?")[0]
        if bucket != "backups":
            return self._answer(404, (
                b"<Error><Code>NoSuchBucket</Code>"
                b"<Message>The specified bucket does not exist</Message></Error>"))
        if self.command == "PUT":
            type(self).objects[key] = body
            return self._answer(200)
        if self.command == "DELETE":
            type(self).objects.pop(key, None)
            return self._answer(204)
        self._answer(405)

    do_PUT = do_DELETE = do_GET = do_HEAD = _handle


@pytest.fixture
def s3():
    FakeS3.objects, FakeS3.requests = {}, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeS3)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield FakeS3, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def _config(endpoint, **overrides):
    fields = {"enabled": True, "endpoint_url": endpoint, "region": "eu-test", "bucket": "backups",
              "prefix": "ci", "access_key_id": "AKIATEST", "secret_access_key": "secret"}
    return RemoteConfig(**{**fields, **overrides})


async def test_upload_puts_the_file_under_the_prefix(s3):
    fake, endpoint = s3

    await remote.upload(_config(endpoint), NAME, b"backup bytes")

    assert fake.objects == {f"ci/{NAME}": b"backup bytes"}


async def test_upload_without_a_prefix(s3):
    fake, endpoint = s3
    await remote.upload(_config(endpoint, prefix=""), NAME, b"x")
    assert list(fake.objects) == [NAME]


async def test_delete_removes_the_object(s3):
    fake, endpoint = s3
    await remote.upload(_config(endpoint), NAME, b"x")

    await remote.delete(_config(endpoint), NAME)

    assert fake.objects == {}


async def test_check_connection_writes_and_removes_a_small_object(s3):
    fake, endpoint = s3

    await remote.check_connection(_config(endpoint))

    assert fake.objects == {}
    assert [method for method, _ in fake.requests] == ["PUT", "DELETE"]
    assert all(path.startswith("/backups/ci/.megooci-connection-test") for _, path in fake.requests)


async def test_a_missing_bucket_is_reported_with_the_storages_reason(s3):
    _, endpoint = s3

    with pytest.raises(RemoteError) as exc:
        await remote.upload(_config(endpoint, bucket="nope"), NAME, b"x")

    assert str(exc.value) == "NoSuchBucket: The specified bucket does not exist"


async def test_check_connection_fails_the_same_way(s3):
    _, endpoint = s3
    with pytest.raises(RemoteError) as exc:
        await remote.check_connection(_config(endpoint, bucket="nope"))
    assert "NoSuchBucket" in str(exc.value)


async def test_an_unreachable_endpoint_is_a_remote_error_not_a_crash(monkeypatch):
    monkeypatch.setattr(remote, "MAX_ATTEMPTS", 1)
    monkeypatch.setattr(remote, "CONNECT_TIMEOUT_SECONDS", 1)
    config = _config("http://127.0.0.1:1")

    with pytest.raises(RemoteError) as exc:
        await remote.upload(config, NAME, b"x")

    assert str(exc.value) and len(str(exc.value)) <= 300
    assert "secret" not in str(exc.value)
```

- [ ] **Step 3: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_remote.py
```

Expected: a collection error, `ImportError: cannot import name 'remote' from 'app.services.backup'`.

- [ ] **Step 4: Write the module**

Create `backend/app/services/backup/remote.py`:

```python
"""The optional copy of each backup to S3-compatible storage."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.services.backup.settings import RemoteConfig

_TEST_OBJECT = ".megooci-connection-test"
_MAX_REASON = 300
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 2


class RemoteError(Exception):
    """The storage refused or could not be reached. The message is the
    storage's own reason, for the administrator."""


def _client(config: RemoteConfig) -> Any:
    # Imported here: boto3 takes a noticeable moment to load, and most
    # servers never turn the remote copy on.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url or None,
        region_name=config.region or "us-east-1",
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        config=Config(
            signature_version="s3v4",
            # Other S3 implementations are reached by address, not by a
            # bucket-named host.
            s3={"addressing_style": "path"} if config.endpoint_url else {},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": MAX_ATTEMPTS},
        ),
    )


def _reason(exc: Exception) -> str:
    error = getattr(exc, "response", {}).get("Error", {}) if hasattr(exc, "response") else {}
    if error.get("Code"):
        text = f"{error['Code']}: {error.get('Message', '')}".strip().rstrip(":")
    else:
        text = str(exc) or type(exc).__name__
    return text[:_MAX_REASON]


async def _run(action: Callable[[], None]) -> None:
    try:
        await asyncio.to_thread(action)
    except Exception as exc:
        raise RemoteError(_reason(exc)) from exc


async def upload(config: RemoteConfig, name: str, data: bytes) -> None:
    def action() -> None:
        _client(config).put_object(
            Bucket=config.bucket, Key=config.object_key(name), Body=data,
            ContentType="application/octet-stream",
        )

    await _run(action)


async def delete(config: RemoteConfig, name: str) -> None:
    def action() -> None:
        _client(config).delete_object(Bucket=config.bucket, Key=config.object_key(name))

    await _run(action)


async def check_connection(config: RemoteConfig) -> None:
    """Write and delete a small object, as a backup would."""
    def action() -> None:
        client = _client(config)
        key = config.object_key(_TEST_OBJECT)
        client.put_object(Bucket=config.bucket, Key=key, Body=b"megooci")
        client.delete_object(Bucket=config.bucket, Key=key)

    await _run(action)
```

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_remote.py
```

Expected: `7 passed` (about ten seconds: one test waits for a connection to be refused).

- [ ] **Step 6: Run the whole backend suite**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `838 passed`. This checks that installing `boto3` did not disturb another package.

- [ ] **Step 7: Commit**

```bash
git add backend/pyproject.toml backend/app/services/backup/remote.py backend/tests/test_backup_remote.py
git commit -m "feat(backup): optional copy to S3-compatible storage

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: The operations

**Files:**
- Create: `backend/app/services/backup/service.py`
- Test: `backend/tests/test_backup_service.py` (new)

**Interfaces:**
- Consumes: everything from Tasks 1–6; `app.core.audit.record`; `app.api.v1.system.is_maintenance_mode` (imported inside the function, as the build executor does); `app.services.search.sync_all`; `app.services.in_app_notifications.notify_users` and `get_admin_user_ids`.
- Every operation takes a session factory (`async_sessionmaker`) and the server's secret key, not a session.
- Produces: `Refused(Exception)`, `PassphraseRequired(WrongPassphrase)`, `RestoreFailed(Exception)`; `CONFIRMATION_WORD = "restore"`; `MAX_UPLOAD_BYTES = 100 * 1024 * 1024`; `UNVERSIONED = "unversioned"`.
- Produces: `async schema_revision(db) -> str` — `alembic_version.version_num`, or `"unversioned"` when the table is missing (the test database).
- Produces: `async create_backup(factory, secret_key, *, kind="manual", actor_id=None, now=None) -> BackupFile`.
- Produces: `store_upload(data: bytes) -> BackupFile` (not async).
- Produces: `async restore_checks(factory, header) -> list[dict]` — `name`, `ok`, `message` for `maintenance`, `builds`, and `schema` when a header is given.
- Produces: `async restore_backup(factory, secret_key, name, *, acting_user_id, confirm, passphrase=None, now=None) -> dict` — `tables`, `pre_restore`, `search_index_rebuilt`.
- Produces: `async delete_backup(factory, secret_key, name, *, actor_id=None) -> str | None` (the remote error, if any), `async retry_remote(factory, secret_key, name) -> dict`, `async check_remote(config)`.
- Produces: `async run_scheduled(factory, secret_key, *, now=None) -> str` — `"not due"`, `"busy"`, `"done"` or `"failed"`; never raises.
- Produces: `async overview(factory, secret_key) -> dict` and `async update_settings(factory, secret_key, *, passphrase=None, schedule=None, remote_config=None, actor_id=None, now=None)`.
- Produces: `async record_event(factory, action, actor_id, metadata)` — an audit entry with target type `backup`; never raises.
- Audit actions: `backup.create`, `backup.restore`, `backup.delete`, `backup.settings` (and `backup.upload`, `backup.download` from the API in Task 8).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backup_service.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_service.py
```

Expected: a collection error, `ImportError: cannot import name 'service' from 'app.services.backup'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/backup/service.py`:

```python
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
                logger.exception("Restore of %s failed; nothing was changed", name)
                raise RestoreFailed(
                    "The backup could not be applied, and nothing was changed. "
                    f"The server reported: {type(exc).__name__}."
                ) from exc

    search_rebuilt = await _rebuild_search_index(factory)
    await record_event(factory, "backup.restore", acting_user_id,
                 {"name": name, "pre_restore": before.name})
    return {"tables": summary, "pre_restore": before.name, "search_index_rebuilt": search_rebuilt}


async def _rebuild_search_index(factory: SessionFactory) -> bool:
    try:
        from app.services import search

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
```

- [ ] **Step 4: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_service.py
```

Expected: `31 passed`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backup/service.py backend/tests/test_backup_service.py
git commit -m "feat(backup): create, restore, schedule and prune backups

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: The admin API and the scheduled task

**Files:**
- Create: `backend/app/api/v1/backups.py`, `backend/app/tasks/backup_tasks.py`
- Modify: `backend/app/api/v1/router.py`, `backend/app/tasks/celery_app.py`
- Test: `backend/tests/test_backup_api.py` (new)

**Interfaces:**
- Consumes: the service (Task 7); `has_global_permission` from `app.core.access`; `get_current_active_user`.
- Produces: `require_global_admin` — the dependency every route has.
- Produces: ten routes under `/api/v1/admin/backups`: `GET ""`, `POST ""`, `POST /upload`, `PUT /settings`, `POST /settings/test-remote`, `GET /{name}/restore-checks`, `GET /{name}/download`, `POST /{name}/restore`, `POST /{name}/upload-remote`, `DELETE /{name}`.
- Errors: invalid name 400, not found 404, busy 409, refused 409, wrong or missing passphrase 422, not a backup 400, invalid settings 400, remote error 502, restore failed 500.
- Produces: Celery task `megooci.scheduled_backup`, and the beat entry `scheduled-backup` every five minutes.
- The route functions use `database.async_session` through the module attribute (`from app import database`), so tests can replace it.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_backup_api.py`:

```python
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
```

- [ ] **Step 2: Run the tests and see them fail**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_api.py
```

Expected: a collection error, `ImportError: cannot import name 'backups' from 'app.api.v1'`.

- [ ] **Step 3: Write the routes and the task**

Create `backend/app/api/v1/backups.py`:

```python
"""Configuration backup and restore — administrators only.

Every route requires a global administrator: a restore can replace every
account and secret on the server, so an admin role held for one project is
not enough. The work is done by ``app.services.backup.service``; this module
checks who is asking and turns the service's refusals into HTTP errors.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from pydantic import BaseModel

from app import database
from app.config import get_settings
from app.core.access import has_global_permission
from app.core.deps import get_current_active_user
from app.models.user import User
from app.services.backup import remote, service, store
from app.services.backup import settings as backup_settings
from app.services.backup.format import BackupFormatError, WrongPassphrase, read_header
from app.services.backup.schedule import InvalidSchedule, Schedule
from app.services.backup.settings import InvalidSettings, RemoteConfig

async def require_global_admin(user: User = Depends(get_current_active_user)) -> User:
    """The signed-in user, if they administer the whole server. A token's
    scopes apply, as everywhere."""
    if not has_global_permission(user, "admin"):
        raise HTTPException(status_code=403, detail="Administrator access required.")
    return user


router = APIRouter(dependencies=[Depends(require_global_admin)])


def _secret_key() -> str:
    return get_settings().MEGOOCI_SECRET_KEY


@contextmanager
def _errors() -> Iterator[None]:
    """Turn what the backup service raises into the HTTP error for it."""
    try:
        yield
    except store.InvalidBackupName:
        raise HTTPException(status_code=400, detail="That is not a backup file name.")
    except store.BackupNotFound:
        raise HTTPException(status_code=404, detail="Backup not found.")
    except store.Busy as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except WrongPassphrase as exc:
        # 422 tells the page to ask for the passphrase the backup was made with.
        raise HTTPException(status_code=422, detail=str(exc))
    except BackupFormatError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except service.Refused as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except (InvalidSettings, InvalidSchedule) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except remote.RemoteError as exc:
        raise HTTPException(status_code=502, detail=f"The remote storage reported: {exc}")
    except service.RestoreFailed as exc:
        raise HTTPException(status_code=500, detail=str(exc))


class ScheduleBody(BaseModel):
    frequency: str = "off"
    time: str = "03:00"
    weekday: int = 0
    keep: int = 14


class RemoteBody(BaseModel):
    enabled: bool = False
    endpoint_url: str = ""
    region: str = ""
    bucket: str = ""
    prefix: str = ""
    access_key_id: str = ""
    # Write-only. Empty keeps the stored key.
    secret_access_key: str = ""

    def to_config(self) -> RemoteConfig:
        return RemoteConfig(**self.model_dump())


class SettingsBody(BaseModel):
    passphrase: str | None = None
    schedule: ScheduleBody | None = None
    remote: RemoteBody | None = None


class RestoreBody(BaseModel):
    confirm: str = ""
    passphrase: str | None = None


@router.get("")
async def list_backups() -> dict[str, Any]:
    """The backups on this server, and the backup settings and last run."""
    with _errors():
        return await service.overview(database.async_session, _secret_key())


@router.post("", status_code=201)
async def create_backup(admin: User = Depends(require_global_admin)) -> dict[str, Any]:
    with _errors():
        backup = await service.create_backup(
            database.async_session, _secret_key(), kind="manual", actor_id=admin.id
        )
    return {"name": backup.name}


@router.post("/upload", status_code=201)
async def upload_backup(
    file: UploadFile, admin: User = Depends(require_global_admin)
) -> dict[str, Any]:
    # One byte past the limit is enough to know the file is too large.
    data = await file.read(service.MAX_UPLOAD_BYTES + 1)
    with _errors():
        backup = service.store_upload(data)
    await service.record_event(database.async_session, "backup.upload", admin.id, {"name": backup.name})
    return {"name": backup.name}


@router.put("/settings")
async def update_settings(
    body: SettingsBody, admin: User = Depends(require_global_admin)
) -> dict[str, Any]:
    with _errors():
        await service.update_settings(
            database.async_session,
            _secret_key(),
            passphrase=body.passphrase,
            schedule=Schedule(**body.schedule.model_dump()) if body.schedule else None,
            remote_config=body.remote.to_config() if body.remote else None,
            actor_id=admin.id,
        )
        return await service.overview(database.async_session, _secret_key())


@router.post("/settings/test-remote")
async def check_remote_settings(body: RemoteBody) -> dict[str, Any]:
    """Try the given remote storage settings without saving them. An empty
    secret access key means the stored one."""
    config = body.to_config()
    if not config.secret_access_key:
        async with database.async_session() as db:
            stored = await backup_settings.get_remote(db, _secret_key())
        config = replace(config, secret_access_key=stored.secret_access_key)
    with _errors():
        # Tested as they would be used: turned on.
        await service.check_remote(replace(config, enabled=True))
    return {"ok": True}


@router.get("/{name}/restore-checks")
async def restore_checks(name: str) -> dict[str, Any]:
    """What a restore of this backup needs, and whether each holds now."""
    with _errors():
        header = read_header(store.read(name))
        checks = await service.restore_checks(database.async_session, header)
    return {"checks": checks, "ready": all(check["ok"] for check in checks)}


@router.get("/{name}/download")
async def download_backup(name: str, admin: User = Depends(require_global_admin)) -> Response:
    with _errors():
        data = store.read(name)
    await service.record_event(database.async_session, "backup.download", admin.id, {"name": name})
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("/{name}/restore")
async def restore_backup(
    name: str, body: RestoreBody, admin: User = Depends(require_global_admin)
) -> dict[str, Any]:
    with _errors():
        return await service.restore_backup(
            database.async_session,
            _secret_key(),
            name,
            acting_user_id=admin.id,
            confirm=body.confirm,
            passphrase=body.passphrase or None,
        )


@router.post("/{name}/upload-remote")
async def upload_remote(name: str) -> dict[str, Any]:
    """Try the remote copy of this backup again."""
    with _errors():
        return await service.retry_remote(database.async_session, _secret_key(), name)


@router.delete("/{name}")
async def delete_backup(name: str, admin: User = Depends(require_global_admin)) -> dict[str, Any]:
    with _errors():
        remote_error = await service.delete_backup(
            database.async_session, _secret_key(), name, actor_id=admin.id
        )
    return {"deleted": name, "remote_error": remote_error}
```

Create `backend/app/tasks/backup_tasks.py`:

```python
import asyncio

from app.tasks.celery_app import celery_app


@celery_app.task(name="megooci.scheduled_backup", bind=True, max_retries=0)
def scheduled_backup(self) -> dict:
    """Periodic (Celery Beat): make the scheduled configuration backup when
    one is due. Whether one is due is decided from the backup settings, so
    the schedule can be changed without restarting anything."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.config import get_settings
    from app.services.backup import service

    settings = get_settings()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    # A fresh engine bound to this task's loop, disposed afterwards: this
    # task fires every few minutes and must not leak a connection pool.
    engine = create_async_engine(settings.MEGOOCI_DATABASE_URL, echo=False, future=True)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        outcome = loop.run_until_complete(
            service.run_scheduled(factory, settings.MEGOOCI_SECRET_KEY)
        )
        return {"outcome": outcome}
    finally:
        try:
            loop.run_until_complete(engine.dispose())
        finally:
            loop.close()
```

- [ ] **Step 4: Mount the routes and schedule the task**

**`backend/app/api/v1/router.py`** — edit 1 of 2. Replace:

````python
    artifacts,
    auth,
    builds,
````

with:

````python
    artifacts,
    auth,
    backups,
    builds,
````

**`backend/app/api/v1/router.py`** — edit 2 of 2. Replace:

````python
api_v1_router.include_router(system.router, prefix="/system", tags=["system"])
````

with:

````python
api_v1_router.include_router(system.router, prefix="/system", tags=["system"])
api_v1_router.include_router(backups.router, prefix="/admin/backups", tags=["backups"])
````

**`backend/app/tasks/celery_app.py`** — edit 1 of 2. Replace:

````python
celery_app.autodiscover_tasks(["app.tasks"], related_name="registry_tasks")
````

with:

````python
celery_app.autodiscover_tasks(["app.tasks"], related_name="registry_tasks")
celery_app.autodiscover_tasks(["app.tasks"], related_name="backup_tasks")
````

**`backend/app/tasks/celery_app.py`** — edit 2 of 2. Replace:

````python
celery_app.conf.beat_schedule = {
````

with:

````python
celery_app.conf.beat_schedule = {
    # Checks whether a scheduled configuration backup is due; the schedule
    # itself is a setting, read on every tick.
    "scheduled-backup": {
        "task": "megooci.scheduled_backup",
        "schedule": timedelta(minutes=5),
        "options": {"queue": "megooci"},
    },
````

- [ ] **Step 5: Run the tests and see them pass**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider tests/test_backup_api.py
```

Expected: `31 passed`.

- [ ] **Step 6: Run the whole backend suite**

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider
```

Expected: `900 passed`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/v1/backups.py backend/app/api/v1/router.py backend/app/tasks/backup_tasks.py backend/app/tasks/celery_app.py backend/tests/test_backup_api.py
git commit -m "feat(backup): admin API and scheduled backup task

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The admin page

**Files:**
- Modify: `frontend/src/lib/api.ts` (a new section before the "Search" section)
- Modify: `frontend/src/components/layout/sidebar.tsx` (one icon import, one link)
- Create: `frontend/src/app/admin/backups/page.tsx`

**Interfaces:**
- Consumes: the routes of Task 8.
- Produces, in `api.ts`: types `BackupKind`, `BackupRemoteStatus`, `BackupEntry`, `BackupSchedule`, `BackupRemoteSettings`, `BackupOverview`, `BackupRestoreCheck`, `BackupRestoreResult`; and `backupsApi` with `overview`, `create`, `upload(file)`, `download(name) → Blob`, `remove`, `restoreChecks`, `restore`, `retryRemote`, `updateSettings`, `testRemote`.
- `fetchRaw` is a small helper for the two requests that are not JSON both ways (upload, download). It must not set a `Content-Type` header: the browser sets the multipart boundary itself.
- `ApiError` is not exported from `api.ts`; the page reads the status of a failed restore as `(err as { status?: number }).status`.

**Behaviour to keep in mind:**
- A restore that answers 422 means the stored passphrase does not open the file: the dialog then shows a passphrase field and keeps the message.
- The Restore button is enabled only when every check is met and the user has typed `restore`.
- The secret access key field is always empty when the page loads; leaving it empty keeps the stored key.

- [ ] **Step 1: Add the API client and the sidebar link**

**`frontend/src/lib/api.ts`**. Replace:

````ts
// ------------------------------------------------------------------
// Search
````

with:

````ts
// ------------------------------------------------------------------
// Configuration backup and restore (administrators only)
// ------------------------------------------------------------------
export type BackupKind = "manual" | "scheduled" | "pre-restore" | "uploaded";

export interface BackupRemoteStatus {
  status: "uploaded" | "failed";
  error: string | null;
  at: string;
}

export interface BackupEntry {
  name: string;
  kind: BackupKind;
  created_at: string;
  size: number;
  /** Null when the file's header cannot be read; `error` then says why. */
  schema_revision: string | null;
  compatible: boolean;
  rows: number | null;
  error: string | null;
  /** Null when no remote copy was attempted. */
  remote: BackupRemoteStatus | null;
}

export interface BackupSchedule {
  frequency: "off" | "daily" | "weekly";
  /** HH:MM, UTC. */
  time: string;
  /** 0 = Monday. Used by "weekly". */
  weekday: number;
  keep: number;
}

export interface BackupRemoteSettings {
  enabled: boolean;
  endpoint_url: string;
  region: string;
  bucket: string;
  prefix: string;
  access_key_id: string;
}

export interface BackupOverview {
  backups: BackupEntry[];
  schema_revision: string;
  passphrase_set: boolean;
  schedule: BackupSchedule;
  remote: BackupRemoteSettings & { secret_access_key_set: boolean };
  last_run: { at: string; ok: boolean; error: string | null; name: string | null } | null;
}

export interface BackupRestoreCheck {
  name: string;
  ok: boolean;
  message: string;
}

export interface BackupRestoreResult {
  tables: Record<string, { written: number; removed: number }>;
  pre_restore: string;
  search_index_rebuilt: boolean;
}

/** A request that is not JSON in one direction: a file upload or download. */
async function fetchRaw(endpoint: string, options: RequestInit): Promise<Response> {
  const send = (token: string | null) =>
    fetch(`${BASE_URL}${endpoint}`, {
      ...options,
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      cache: "no-store",
    });
  let res: Response;
  try {
    res = await send(getAccessToken());
    if (res.status === 401) {
      const newAccess = await refreshAccessTokenOnce();
      if (newAccess) res = await send(newAccess);
    }
  } catch {
    throw new ApiError(
      0,
      null,
      "Couldn't reach the server. Please check your connection and try again.",
    );
  }
  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    throw new ApiError(res.status, body, extractErrorMessage(res.status, body));
  }
  return res;
}

const BACKUPS = "/api/v1/admin/backups";

export const backupsApi = {
  overview: () => fetchApi<BackupOverview>(BACKUPS),

  create: () => fetchApi<{ name: string }>(BACKUPS, { method: "POST" }),

  upload: async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetchRaw(`${BACKUPS}/upload`, { method: "POST", body: form });
    return (await res.json()) as { name: string };
  },

  download: async (name: string) => {
    const res = await fetchRaw(`${BACKUPS}/${encodeURIComponent(name)}/download`, {
      method: "GET",
    });
    return res.blob();
  },

  remove: (name: string) =>
    fetchApi<{ deleted: string; remote_error: string | null }>(
      `${BACKUPS}/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),

  restoreChecks: (name: string) =>
    fetchApi<{ checks: BackupRestoreCheck[]; ready: boolean }>(
      `${BACKUPS}/${encodeURIComponent(name)}/restore-checks`,
    ),

  /** Rejects with status 422 when the backup needs another passphrase. */
  restore: (name: string, body: { confirm: string; passphrase?: string | null }) =>
    fetchApi<BackupRestoreResult>(`${BACKUPS}/${encodeURIComponent(name)}/restore`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  retryRemote: (name: string) =>
    fetchApi<BackupRemoteStatus>(`${BACKUPS}/${encodeURIComponent(name)}/upload-remote`, {
      method: "POST",
    }),

  /** Only the given parts change. An empty secret key keeps the stored one. */
  updateSettings: (body: {
    passphrase?: string;
    schedule?: BackupSchedule;
    remote?: BackupRemoteSettings & { secret_access_key: string };
  }) =>
    fetchApi<BackupOverview>(`${BACKUPS}/settings`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  testRemote: (body: BackupRemoteSettings & { secret_access_key: string }) =>
    fetchApi<{ ok: boolean }>(`${BACKUPS}/settings/test-remote`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

// ------------------------------------------------------------------
// Search
````

**`frontend/src/components/layout/sidebar.tsx`** — edit 1 of 2. Replace:

````tsx
  Plug,
  Container,
} from "lucide-react";
````

with:

````tsx
  Plug,
  Container,
  DatabaseBackup,
} from "lucide-react";
````

**`frontend/src/components/layout/sidebar.tsx`** — edit 2 of 2. Replace:

````tsx
  { href: "/admin/users", label: "Users", icon: Users, adminOnly: true },
````

with:

````tsx
  { href: "/admin/users", label: "Users", icon: Users, adminOnly: true },
  { href: "/admin/backups", label: "Backups", icon: DatabaseBackup, adminOnly: true },
````

- [ ] **Step 2: Create the page**

Create `frontend/src/app/admin/backups/page.tsx`:

```tsx
"use client";

import * as React from "react";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  CloudOff,
  CloudUpload,
  DatabaseBackup,
  Download,
  Loader2,
  RotateCcw,
  Trash2,
  Upload,
  XCircle,
} from "lucide-react";
import { AppLayout } from "@/components/layout/app-layout";
import { RequireAdmin } from "@/components/require-permission";
import {
  backupsApi,
  type BackupEntry,
  type BackupOverview,
  type BackupRestoreCheck,
  type BackupSchedule,
} from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const KIND_LABEL: Record<BackupEntry["kind"], string> = {
  manual: "Manual",
  scheduled: "Scheduled",
  "pre-restore": "Before restore",
  uploaded: "Uploaded",
};

function message(err: unknown): string {
  return err instanceof Error && err.message ? err.message : "Something went wrong.";
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

function scheduleInWords(schedule: BackupSchedule): string {
  if (schedule.frequency === "off") return "No scheduled backups.";
  const when =
    schedule.frequency === "daily"
      ? `every day at ${schedule.time} UTC`
      : `every ${WEEKDAYS[schedule.weekday]} at ${schedule.time} UTC`;
  return `A backup is made ${when}; the newest ${schedule.keep} are kept.`;
}

/** The restore dialog: what a restore does, what it needs, and the confirmation. */
function RestoreDialog({
  backup,
  onClose,
  onRestored,
}: {
  backup: BackupEntry | null;
  onClose: () => void;
  onRestored: () => void;
}) {
  const [checks, setChecks] = React.useState<BackupRestoreCheck[] | null>(null);
  const [confirm, setConfirm] = React.useState("");
  const [passphrase, setPassphrase] = React.useState("");
  const [needsPassphrase, setNeedsPassphrase] = React.useState(false);
  const [problem, setProblem] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState(false);
  const name = backup?.name ?? null;

  React.useEffect(() => {
    setChecks(null);
    setConfirm("");
    setPassphrase("");
    setNeedsPassphrase(false);
    setProblem(null);
    if (!name) return;
    let current = true;
    backupsApi
      .restoreChecks(name)
      .then((result) => current && setChecks(result.checks))
      .catch((err) => current && setProblem(message(err)));
    return () => {
      current = false;
    };
  }, [name]);

  async function restore() {
    if (!name) return;
    setBusy(true);
    setProblem(null);
    try {
      const result = await backupsApi.restore(name, {
        confirm,
        passphrase: needsPassphrase ? passphrase : null,
      });
      toast.success(`Restored. The previous state was saved as ${result.pre_restore}.`);
      if (!result.search_index_rebuilt) {
        toast.warning("The search index could not be rebuilt; search may be out of date.");
      }
      onRestored();
      onClose();
    } catch (err) {
      // 422: the stored passphrase does not open this file.
      if ((err as { status?: number }).status === 422) setNeedsPassphrase(true);
      setProblem(message(err));
    } finally {
      setBusy(false);
    }
  }

  const ready = checks !== null && checks.every((check) => check.ok);

  return (
    <Dialog open={backup !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Restore this backup?</DialogTitle>
          <DialogDescription>
            {backup && `${KIND_LABEL[backup.kind]} backup from ${formatTime(backup.created_at)}`}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          <div className="rounded-md border border-warning/40 bg-warning/10 p-3 leading-relaxed">
            The server&apos;s configuration will be replaced with this backup: users, roles,
            projects, pipelines, secrets, channels, agents and settings. Anything created since
            the backup is removed, together with its build history. Build history of everything
            that still exists is kept. Your own account keeps its password and stays an
            administrator. A backup of the current state is made first.
          </div>

          <ul className="space-y-1.5">
            {checks === null && !problem && (
              <li className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking…
              </li>
            )}
            {checks?.map((check) => (
              <li key={check.name} className="flex items-start gap-2">
                {check.ok ? (
                  <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                ) : (
                  <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
                )}
                <span>{check.message}</span>
              </li>
            ))}
          </ul>

          {needsPassphrase && (
            <label className="block space-y-1.5">
              <span className="font-medium">Passphrase this backup was made with</span>
              <Input
                type="password"
                autoComplete="off"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </label>
          )}

          <label className="block space-y-1.5">
            <span className="font-medium">
              Type <code className="rounded bg-muted px-1">restore</code> to confirm
            </span>
            <Input
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              autoComplete="off"
              disabled={!ready}
            />
          </label>

          {problem && <p className="text-destructive">{problem}</p>}
        </div>

        <DialogFooter>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={restore}
            disabled={busy || !ready || confirm !== "restore" || (needsPassphrase && !passphrase)}
          >
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Restore
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Passphrase, schedule and remote storage. Each part is saved on its own. */
function BackupSettings({
  overview,
  onSaved,
}: {
  overview: BackupOverview;
  onSaved: (overview: BackupOverview) => void;
}) {
  const [passphrase, setPassphrase] = React.useState("");
  const [schedule, setSchedule] = React.useState(overview.schedule);
  const [remote, setRemote] = React.useState({ ...overview.remote, secret_access_key: "" });
  const [busy, setBusy] = React.useState<string | null>(null);

  async function run(what: string, action: () => Promise<void>) {
    setBusy(what);
    try {
      await action();
    } catch (err) {
      toast.error(message(err));
    } finally {
      setBusy(null);
    }
  }

  function remoteBody() {
    return {
      enabled: remote.enabled,
      endpoint_url: remote.endpoint_url.trim(),
      region: remote.region.trim(),
      bucket: remote.bucket.trim(),
      prefix: remote.prefix.trim(),
      access_key_id: remote.access_key_id.trim(),
      secret_access_key: remote.secret_access_key,
    };
  }

  const field = "block space-y-1.5 text-sm";

  return (
    <Card>
      <CardHeader>
        <CardTitle>Settings</CardTitle>
        <CardDescription>
          These belong to this server. They are not part of a backup and a restore does not
          change them.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-8">
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Passphrase</h3>
          <p className="text-sm text-muted-foreground">
            Every backup is encrypted with this passphrase.{" "}
            <strong>
              A backup cannot be restored without the passphrase it was made with, and the
              passphrase cannot be recovered from the server.
            </strong>{" "}
            Changing it affects new backups only.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className={`${field} min-w-64 flex-1`}>
              <span className="font-medium">
                {overview.passphrase_set ? "New passphrase" : "Passphrase"} (at least 12
                characters)
              </span>
              <Input
                type="password"
                autoComplete="new-password"
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
              />
            </label>
            <Button
              type="button"
              disabled={busy !== null || passphrase.length < 12}
              onClick={() =>
                run("passphrase", async () => {
                  onSaved(await backupsApi.updateSettings({ passphrase }));
                  setPassphrase("");
                  toast.success("Passphrase saved. Keep a copy of it somewhere safe.");
                })
              }
            >
              {overview.passphrase_set ? "Change passphrase" : "Set passphrase"}
            </Button>
          </div>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Schedule</h3>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className={field}>
              <span className="font-medium">Frequency</span>
              <Select
                value={schedule.frequency}
                onChange={(e) =>
                  setSchedule({
                    ...schedule,
                    frequency: e.target.value as BackupSchedule["frequency"],
                  })
                }
                options={[
                  { value: "off", label: "Off" },
                  { value: "daily", label: "Daily" },
                  { value: "weekly", label: "Weekly" },
                ]}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Time (UTC)</span>
              <Input
                type="time"
                value={schedule.time}
                disabled={schedule.frequency === "off"}
                onChange={(e) => setSchedule({ ...schedule, time: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Day</span>
              <Select
                value={String(schedule.weekday)}
                disabled={schedule.frequency !== "weekly"}
                onChange={(e) => setSchedule({ ...schedule, weekday: Number(e.target.value) })}
                options={WEEKDAYS.map((label, index) => ({ value: String(index), label }))}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Scheduled backups to keep</span>
              <Input
                type="number"
                min={1}
                max={365}
                value={schedule.keep}
                disabled={schedule.frequency === "off"}
                onChange={(e) => setSchedule({ ...schedule, keep: Number(e.target.value) })}
              />
            </label>
          </div>
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null}
            onClick={() =>
              run("schedule", async () => {
                onSaved(await backupsApi.updateSettings({ schedule }));
                toast.success("Schedule saved.");
              })
            }
          >
            Save schedule
          </Button>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Remote copy</h3>
          <p className="text-sm text-muted-foreground">
            Optional. When on, every backup is also copied to S3-compatible storage (AWS S3,
            MinIO, Backblaze B2 and others). Restoring uses the files on this server: to
            restore a backup that exists only in the bucket, download it there and upload it
            here.
          </p>
          <label className="flex items-center gap-2 text-sm font-medium">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={remote.enabled}
              onChange={(e) => setRemote({ ...remote, enabled: e.target.checked })}
            />
            Copy every backup to remote storage
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className={field}>
              <span className="font-medium">Endpoint URL (empty for AWS)</span>
              <Input
                placeholder="https://s3.example.com"
                value={remote.endpoint_url}
                onChange={(e) => setRemote({ ...remote, endpoint_url: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Region</span>
              <Input
                placeholder="us-east-1"
                value={remote.region}
                onChange={(e) => setRemote({ ...remote, region: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Bucket</span>
              <Input
                value={remote.bucket}
                onChange={(e) => setRemote({ ...remote, bucket: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Folder in the bucket (optional)</span>
              <Input
                placeholder="megooci/backups"
                value={remote.prefix}
                onChange={(e) => setRemote({ ...remote, prefix: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Access key ID</span>
              <Input
                autoComplete="off"
                value={remote.access_key_id}
                onChange={(e) => setRemote({ ...remote, access_key_id: e.target.value })}
              />
            </label>
            <label className={field}>
              <span className="font-medium">Secret access key</span>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={
                  overview.remote.secret_access_key_set ? "Saved — leave empty to keep it" : ""
                }
                value={remote.secret_access_key}
                onChange={(e) => setRemote({ ...remote, secret_access_key: e.target.value })}
              />
            </label>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              disabled={busy !== null}
              onClick={() =>
                run("test", async () => {
                  await backupsApi.testRemote(remoteBody());
                  toast.success("The remote storage accepted a test file.");
                })
              }
            >
              {busy === "test" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Test connection
            </Button>
            <Button
              type="button"
              variant="outline"
              disabled={busy !== null}
              onClick={() =>
                run("remote", async () => {
                  onSaved(await backupsApi.updateSettings({ remote: remoteBody() }));
                  setRemote((current) => ({ ...current, secret_access_key: "" }));
                  toast.success("Remote storage settings saved.");
                })
              }
            >
              Save remote storage
            </Button>
          </div>
        </section>
      </CardContent>
    </Card>
  );
}

function RemoteBadge({ backup, enabled }: { backup: BackupEntry; enabled: boolean }) {
  if (backup.remote?.status === "uploaded") {
    return (
      <span className="flex items-center gap-1 text-success">
        <CloudUpload className="h-3.5 w-3.5" /> Copied
      </span>
    );
  }
  if (backup.remote?.status === "failed") {
    return (
      <span className="flex items-center gap-1 text-destructive" title={backup.remote.error ?? ""}>
        <CloudOff className="h-3.5 w-3.5" /> Failed
      </span>
    );
  }
  return <span className="text-muted-foreground">{enabled ? "Not copied" : "—"}</span>;
}

function BackupsPage() {
  const [overview, setOverview] = React.useState<BackupOverview | null>(null);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [busy, setBusy] = React.useState<string | null>(null);
  const [restoring, setRestoring] = React.useState<BackupEntry | null>(null);
  const fileInput = React.useRef<HTMLInputElement>(null);

  const load = React.useCallback(async () => {
    try {
      setOverview(await backupsApi.overview());
      setLoadError(null);
    } catch (err) {
      setLoadError(message(err));
    }
  }, []);

  React.useEffect(() => {
    load();
  }, [load]);

  async function run(what: string, action: () => Promise<void>) {
    setBusy(what);
    try {
      await action();
      await load();
    } catch (err) {
      toast.error(message(err));
    } finally {
      setBusy(null);
    }
  }

  async function download(name: string) {
    const blob = await backupsApi.download(name);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (loadError && !overview) {
    return <p className="text-sm text-destructive">{loadError}</p>;
  }
  if (!overview) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const lastRun = overview.last_run;

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <DatabaseBackup className="h-6 w-6" /> Backups
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            A backup holds the server&apos;s configuration: users, roles, projects, pipelines,
            secrets, channels, agents and settings. It does not hold build history, logs,
            artifacts or registry images.
          </p>
        </div>
        <div className="flex gap-2">
          <input
            ref={fileInput}
            type="file"
            accept=".mcbak"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file) {
                run("upload", async () => {
                  await backupsApi.upload(file);
                  toast.success("Backup uploaded.");
                });
              }
            }}
          />
          <Button
            type="button"
            variant="outline"
            disabled={busy !== null}
            onClick={() => fileInput.current?.click()}
          >
            <Upload className="mr-2 h-4 w-4" /> Upload backup
          </Button>
          <Button
            type="button"
            disabled={busy !== null || !overview.passphrase_set}
            title={overview.passphrase_set ? undefined : "Set a passphrase first"}
            onClick={() =>
              run("create", async () => {
                await backupsApi.create();
                toast.success("Backup created.");
              })
            }
          >
            {busy === "create" ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <DatabaseBackup className="mr-2 h-4 w-4" />
            )}
            Create backup
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="space-y-1.5 pt-6 text-sm">
          {!overview.passphrase_set && (
            <p className="flex items-center gap-2 text-warning">
              <AlertTriangle className="h-4 w-4" /> No passphrase is set. Set one below before
              the first backup.
            </p>
          )}
          <p>{scheduleInWords(overview.schedule)}</p>
          {lastRun && (
            <p className={lastRun.ok ? "text-muted-foreground" : "text-destructive"}>
              Last scheduled backup: {formatTime(lastRun.at)} —{" "}
              {lastRun.ok ? "succeeded" : `failed: ${lastRun.error}`}
            </p>
          )}
          <p className="text-muted-foreground">
            Remote copy: {overview.remote.enabled ? `on (bucket ${overview.remote.bucket})` : "off"}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Backups on this server</CardTitle>
          <CardDescription>
            These files are on the server&apos;s own disk and do not survive losing it. Download
            the ones you need to keep, or turn on the remote copy.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {overview.backups.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">No backups yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <th className="py-2 pr-4 font-medium">Made</th>
                    <th className="py-2 pr-4 font-medium">Kind</th>
                    <th className="py-2 pr-4 font-medium">Size</th>
                    <th className="py-2 pr-4 font-medium">Remote</th>
                    <th className="py-2 font-medium text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.backups.map((backup) => (
                    <tr key={backup.name} className="border-b last:border-0">
                      <td className="py-2.5 pr-4">
                        <div>{formatTime(backup.created_at)}</div>
                        <div className="font-mono text-xs text-muted-foreground">{backup.name}</div>
                        {backup.error && (
                          <div className="text-xs text-destructive">{backup.error}</div>
                        )}
                        {!backup.error && !backup.compatible && (
                          <div className="text-xs text-warning">
                            Made with database version {backup.schema_revision}; this server is
                            at {overview.schema_revision}. It cannot be restored here.
                          </div>
                        )}
                      </td>
                      <td className="py-2.5 pr-4">
                        <Badge variant="outline">{KIND_LABEL[backup.kind]}</Badge>
                      </td>
                      <td className="py-2.5 pr-4 whitespace-nowrap">{formatSize(backup.size)}</td>
                      <td className="py-2.5 pr-4 whitespace-nowrap">
                        <RemoteBadge backup={backup} enabled={overview.remote.enabled} />
                      </td>
                      <td className="py-2.5">
                        <div className="flex justify-end gap-1">
                          {overview.remote.enabled && backup.remote?.status !== "uploaded" && (
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              disabled={busy !== null}
                              title="Copy to remote storage"
                              onClick={() =>
                                run(`remote:${backup.name}`, async () => {
                                  const status = await backupsApi.retryRemote(backup.name);
                                  if (status.status === "failed") {
                                    throw new Error(`The remote storage reported: ${status.error}`);
                                  }
                                  toast.success("Copied to remote storage.");
                                })
                              }
                            >
                              <CloudUpload className="h-4 w-4" />
                            </Button>
                          )}
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== null}
                            title="Download"
                            onClick={() => run(`download:${backup.name}`, () => download(backup.name))}
                          >
                            <Download className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== null || !backup.compatible}
                            title={backup.compatible ? "Restore" : "Cannot be restored on this server"}
                            onClick={() => setRestoring(backup)}
                          >
                            <RotateCcw className="h-4 w-4" />
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            disabled={busy !== null}
                            title="Delete"
                            onClick={() => {
                              if (!window.confirm(`Delete ${backup.name}? This cannot be undone.`)) {
                                return;
                              }
                              run(`delete:${backup.name}`, async () => {
                                const result = await backupsApi.remove(backup.name);
                                if (result.remote_error) {
                                  toast.warning(
                                    `Deleted here, but the remote copy could not be deleted: ${result.remote_error}`,
                                  );
                                } else {
                                  toast.success("Backup deleted.");
                                }
                              });
                            }}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>

      <BackupSettings overview={overview} onSaved={setOverview} />

      <RestoreDialog backup={restoring} onClose={() => setRestoring(null)} onRestored={load} />
    </div>
  );
}

export default function AdminBackupsPage() {
  return (
    <AppLayout>
      <RequireAdmin
        fallback={
          <p className="text-sm text-muted-foreground">
            Only administrators can manage backups.
          </p>
        }
      >
        <BackupsPage />
      </RequireAdmin>
    </AppLayout>
  );
}
```

- [ ] **Step 3: Type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/components/layout/sidebar.tsx frontend/src/app/admin/backups/page.tsx
git commit -m "feat(backup): admin page for backups, restore and settings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: README

**Files:**
- Modify: `README.md` (a new section before "Running a Self-Hosted Build Agent")

- [ ] **Step 1: Add the section**

**`README.md`**. Replace:

````markdown
## Running a Self-Hosted Build Agent
````

with:

````markdown
## Backup and Restore

Administrators can back up and restore the server's **configuration** under **Backups** in the sidebar: users, roles, projects, pipelines, secrets, notification channels, git connections, agents, API tokens and system settings. Build history, logs, artifacts and registry images are not part of a backup; back up the `megooci_storage` and `postgres_data` volumes for those.

- **Passphrase.** Set a backup passphrase first. Every backup is encrypted with it, and it is the only way to open one: it cannot be recovered from the server. A backup made on one server can be restored on another, even with a different `MEGOOCI_SECRET_KEY`, given the passphrase.
- **Where backups live.** Files are written to `<MEGOOCI_STORAGE_ROOT>/backups/` on the storage volume. That disk can be lost with the server, so download the backups you need or turn on the remote copy.
- **Schedule.** Daily or weekly at a time in UTC, keeping the newest N scheduled backups. Needs the `celery-worker` and `celery-beat` services, which the default Compose file runs.
- **Remote copy (optional).** Every backup can also be uploaded to S3-compatible storage (AWS S3, MinIO, Backblaze B2, …). Enter the endpoint, bucket and keys, and use **Test connection**.
- **Restore.** Turn maintenance mode on, wait for running builds to finish, then choose **Restore** and type `restore`. The configuration becomes exactly what the backup holds: anything created since is removed together with its build history, and the build history of everything that still exists is kept. A backup of the current state is taken first, and the administrator doing the restore keeps their password and access. A backup can only be restored by the release that has the same database version as the one that made it.

## Running a Self-Hosted Build Agent
````

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs(backup): document backup and restore

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Final verification

- [ ] **Step 1: Backend suite**

Run from `backend/`:

```bash
./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider -W error::RuntimeWarning
```

Expected: `900 passed`.

- [ ] **Step 2: Frontend type-check**

Run from `frontend/`:

```bash
npx tsc --noEmit
```

Expected: no output, exit code 0.

- [ ] **Step 3: Nothing outside the plan changed**

Run from the repository root:

```bash
git diff main --stat -- agent backend/alembic backend/app/models backend/app/config.py
```

Expected: no output — no agent change, no migration, no model change, no setting.

- [ ] **Step 4: Manual checks (need the running stack with PostgreSQL, the Celery worker and beat, and for the remote copy an S3-compatible bucket such as a local MinIO)**

Report each as passed, failed or not run. Do not report them as passed without doing them.

1. As an administrator, open Backups. Without a passphrase, Create backup is disabled. Set a passphrase; create a backup; it appears in the list and downloads.
2. As a non-administrator, the Backups link is not shown and `/admin/backups` shows the "only administrators" message; the API answers 403.
3. Restore with maintenance mode off: the dialog shows which check is not met and the button stays disabled. Turn maintenance mode on; change a pipeline; restore; the pipeline is back, a "Before restore" backup appeared, and maintenance mode is still on.
4. Delete a project created after a backup by restoring that backup: the project and its builds are gone; builds of other pipelines are still there.
5. Change the passphrase, then restore a backup made before the change: the dialog asks for the old passphrase and accepts it.
6. Upload the downloaded file on another server (or after wiping this one) that has a different `MEGOOCI_SECRET_KEY`; restore it; a pipeline that uses a secret builds successfully.
7. Set the schedule to a time two minutes ahead (UTC): a scheduled backup appears within about five minutes of that time, and the status line shows the run.
8. Configure the remote copy against a bucket; Test connection succeeds; a new backup shows "Copied" and the object is in the bucket; deleting the backup removes the object. With wrong keys: Test connection reports the storage's error, and a backup is still made and shows "Failed" with a retry button.
9. On PostgreSQL specifically: restore while another administrator is browsing, and confirm the restore completes and nothing is left half-applied.
