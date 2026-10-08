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
