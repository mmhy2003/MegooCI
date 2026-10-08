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
