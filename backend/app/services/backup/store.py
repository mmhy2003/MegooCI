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
