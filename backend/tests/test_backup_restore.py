"""Restoring makes the configuration equal to the backup's, keeps the history
of what still exists, and changes nothing when it cannot finish."""
import copy
import os
import uuid
from datetime import datetime, timezone

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


async def _comparable(sf, key=SECRET_KEY, *, agents_as_restored_elsewhere=False):
    """The configuration with encrypted values shown in the clear, so two
    servers with different keys can be compared. On another server a restored
    agent has never connected, which is the one way its row may differ."""
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
            if spec.name == "agents" and agents_as_restored_elsewhere:
                row.update(status="offline", connected_at=None, last_seen_at=None,
                           agent_version=None, current_build_id=None)
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

    expected = await _comparable(sf, agents_as_restored_elsewhere=True)
    restored = await _comparable(other_server, key=OTHER_SECRET_KEY)
    assert [(a["status"], a["connected_at"]) for a in restored["agents"]] == [("offline", None)]
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


async def test_pending_builds_are_cancelled_and_connected_agents_stay_usable(sf):
    """A connected agent only reports that it is alive; it does not connect
    again. If the restore wrote it as never connected, no build would be
    sent to it until someone restarted it."""
    from app.models.agent import Agent
    from app.models.build import Build, Stage, Step
    from app.services.agent_dispatcher import pick_online_agent

    ids = await _seed(sf)
    async with sf() as db:
        pending = await insert(db, Build, pipeline_id=ids["pipeline"], number=5, status="pending")
        stage = await insert(db, Stage, build_id=pending, name="s", sort_order=0, status="pending")
        step = await insert(db, Step, stage_id=stage, name="t", sort_order=0, status="pending")
        # The agent is reserved for the pending build, as the dispatcher leaves it.
        await db.execute(sa.update(Agent.__table__).values(
            current_build_id=pending, agent_version="1.4.0",
            last_seen_at=datetime(2026, 1, 2, 0, 5, tzinfo=timezone.utc)))
        await db.commit()
        connected_before = (await rows(db, Agent))[0]["connected_at"]

    await _restore(sf, await _export(sf), ids["admin"])

    async with sf() as db:
        builds = {row["id"]: row for row in await rows(db, Build)}
        stages = {row["id"]: row["status"] for row in await rows(db, Stage)}
        steps = {row["id"]: row["status"] for row in await rows(db, Step)}
        agent = (await rows(db, Agent))[0]
    assert builds[pending]["status"] == "cancelled" and builds[pending]["finished_at"] is not None
    assert (stages[stage], steps[step]) == ("cancelled", "cancelled")
    assert builds[ids["build"]]["status"] == "success" and stages[ids["stage"]] == "success"
    assert agent["current_build_id"] is None, "the reservation for the cancelled build is gone"
    assert (agent["status"], agent["agent_version"]) == ("online", "1.4.0")
    assert agent["connected_at"] == connected_before and agent["last_seen_at"] is not None
    async with sf() as db:
        picked = await pick_online_agent(db)
    assert picked is not None and picked.id == ids["agent"], "builds can be sent to it"


async def test_values_handed_from_one_surviving_row_to_another_are_put_back(sf):
    """Since the backup, one account took over the email another one used to
    have. Rows are rewritten one at a time, in an order that depends on their
    ids, so both directions are tried."""
    from app.models.user import User

    ids = await _seed(sf)
    payload = await _export(sf)
    for giver, taker in (("dev", "admin"), ("admin", "dev")):
        given = f"{giver}@example.com"
        async with sf() as db:
            await db.execute(sa.update(User.__table__).where(User.id == ids[giver])
                             .values(email=f"{giver}-moved@example.com"))
            await db.execute(sa.update(User.__table__).where(User.id == ids[taker])
                             .values(email=given))
            await db.commit()

        await _restore(sf, payload, ids["admin"])

        async with sf() as db:
            emails = {row["id"]: row["email"] for row in await rows(db, User)}
        assert emails == {ids["admin"]: "admin@example.com", ids["dev"]: "dev@example.com"}


async def test_two_surviving_rows_that_exchanged_names_are_put_back(sf):
    from app.models.project import Project

    ids = await _seed(sf)
    payload = await _export(sf)
    async with sf() as db:
        for project, name, slug in ((ids["project"], "tmp", "tmp"),
                                    (ids["parent_project"], "Web", "web"),
                                    (ids["project"], "Platform", "platform")):
            await db.execute(sa.update(Project.__table__).where(Project.id == project)
                             .values(name=name, slug=slug))
        await db.commit()

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        names = {row["id"]: (row["name"], row["slug"]) for row in await rows(db, Project)}
    assert names == {ids["project"]: ("Web", "web"), ids["parent_project"]: ("Platform", "platform")}


async def test_an_empty_json_column_stays_empty_in_the_database(sf):
    """Found on PostgreSQL: a column that was NULL came back holding the JSON
    value null, which the database does not treat as empty."""
    ids = await _seed(sf)
    empty = "SELECT token_scopes IS NULL FROM git_provider_connections"
    async with sf() as db:
        assert (await db.execute(sa.text(empty))).scalar() in (True, 1)

    await _restore(sf, await _export(sf), ids["admin"])

    async with sf() as db:
        assert (await db.execute(sa.text(empty))).scalar() in (True, 1)


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
