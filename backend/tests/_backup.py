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
