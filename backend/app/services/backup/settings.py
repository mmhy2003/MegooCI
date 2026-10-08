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
