"""Two-step verification in backups: it travels with the accounts, and a
restore never changes it for the administrator who runs the restore."""
import base64
import copy
import os
import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.config import get_settings
from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.models.user import User
from app.services.backup.export import export_configuration
from app.services.backup.restore import apply_configuration
from app.services.backup.tables import TABLES
from app.services.two_step import passkeys, recovery_codes, sign_in, status, totp
from app.services.two_step.reset import reset
from tests._backup import OTHER_SECRET_KEY, SECRET_KEY, decrypt, insert, seed_world
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, STEP, add_passkey, code_at, turn_on_totp


@pytest_asyncio.fixture
async def sf(monkeypatch):
    # The authenticator secret is encrypted with the server's key; the backup
    # helpers call that key SECRET_KEY.
    monkeypatch.setattr(get_settings(), "MEGOOCI_SECRET_KEY", SECRET_KEY)
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def other_server():
    """A second, empty server with another secret key."""
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _world(sf) -> dict:
    """The usual configuration, where the developer has every kind of method,
    set up for real so that codes and the passkey can be tried afterwards."""
    async with sf() as db:
        ids = await seed_world(db)
        dev = await db.get(User, ids["dev"])
        await reset(db, dev.id)  # the placeholders seed_world gives the developer
        secret, codes = await turn_on_totp(db, dev)
        device, _ = await add_passkey(db, dev, name="Dev laptop")
        await db.commit()
    return {**ids, "secret": secret, "codes": codes, "device": device}


async def _export(sf, key=SECRET_KEY) -> dict:
    async with sf() as db:
        payload, _ = await export_configuration(db, key)
    return payload


async def _restore(sf, payload, acting, key=SECRET_KEY) -> None:
    async with sf() as db:
        await apply_configuration(db, payload, key, acting_user_id=acting)
        await db.commit()


async def _passkey_works(db, user_id, device) -> bool:
    options, challenge = await passkeys.authentication_options(db, user_id)
    try:
        await passkeys.verify(db, user_id, device.get(options), challenge, NOW)
    except passkeys.PasskeyError:
        return False
    return True


# ── what a backup holds ─────────────────────────────────────────────────

def test_the_three_account_tables_are_backed_up_and_tickets_are_not():
    names = [spec.name for spec in TABLES]

    assert names.index("users") < names.index("user_two_step")
    assert {"user_two_step", "user_passkeys", "user_recovery_codes"} <= set(names)
    assert TwoStepTicket.__tablename__ not in names
    by_name = {spec.name: spec for spec in TABLES}
    assert by_name["user_two_step"].encrypted == ("totp_secret_encrypted",)


async def test_the_authenticator_secret_leaves_the_server_in_the_clear_inside_the_file(sf):
    """The backup file is encrypted as a whole with the passphrase. Inside it
    the secret is not tied to this server's key, so another server can use it."""
    world = await _world(sf)
    payload = await _export(sf)
    (row,) = payload["tables"]["user_two_step"]

    assert set(row["totp_secret_encrypted"]) == {"$enc"}
    assert base64.b64decode(row["totp_secret_encrypted"]["$enc"]).decode() == world["secret"]
    # Recovery codes stay hashed, in the backup as in the database.
    hashes = [code["code_hash"] for code in payload["tables"]["user_recovery_codes"]]
    assert len(hashes) == 10 and all(h.startswith("$2") for h in hashes)
    assert not any(code.replace("-", "") in repr(payload) for code in world["codes"])


async def test_a_pending_sign_in_is_not_in_a_backup(sf):
    world = await _world(sf)
    async with sf() as db:
        await sign_in.decide(db, await db.get(User, world["dev"]), NOW)
        await db.commit()

    assert "two_step_tickets" not in (await _export(sf))["tables"]


# ── restoring ───────────────────────────────────────────────────────────

async def test_everything_works_again_on_a_server_with_another_key(sf, other_server, monkeypatch):
    world = await _world(sf)
    async with sf() as db:
        await status.set_required(db, True)
        await db.commit()
    payload = await _export(sf)
    async with other_server() as db:
        bootstrap = await insert(db, User, email="bootstrap@example.com", is_admin=True, is_active=True)
        await db.commit()

    await _restore(other_server, payload, bootstrap, key=OTHER_SECRET_KEY)

    monkeypatch.setattr(get_settings(), "MEGOOCI_SECRET_KEY", OTHER_SECRET_KEY)
    async with other_server() as db:
        dev = world["dev"]
        stored = (await db.get(UserTwoStep, dev)).totp_secret_encrypted
        assert decrypt(stored, OTHER_SECRET_KEY) == world["secret"]
        assert await status.methods(db, dev) == ["totp", "passkey"]
        later = NOW + 5 * STEP
        assert await totp.check(db, dev, code_at(world["secret"], later), later) is True
        assert await _passkey_works(db, dev, world["device"]) is True
        assert await recovery_codes.use(db, dev, world["codes"][0], NOW) is True
        assert await status.is_required(db) is True


async def test_a_restore_puts_other_accounts_back_as_the_backup_has_them(sf):
    world = await _world(sf)
    payload = await _export(sf)
    async with sf() as db:
        dev = await db.get(User, world["dev"])
        await reset(db, dev.id)                      # since the backup: reset,
        new_secret, _ = await turn_on_totp(db, dev)  # set up again with another phone
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await totp.check(db, dev.id, code_at(new_secret, later), later) is False
        assert await totp.check(db, dev.id, code_at(world["secret"], later), later) is True
        assert await _passkey_works(db, dev.id, world["device"]) is True
        assert await recovery_codes.remaining(db, dev.id) == 10


async def test_a_restore_removes_what_was_added_since(sf):
    ids = await _world(sf)
    payload = await _export(sf)
    async with sf() as db:
        newcomer = await insert(db, User, email="new@example.com", is_active=True)
        await add_passkey(db, await db.get(User, newcomer))
        await db.commit()

    await _restore(sf, payload, ids["admin"])

    async with sf() as db:
        owners = set((await db.execute(sa.select(UserPasskey.user_id))).scalars())
        assert owners == {ids["dev"]}
        assert (await db.execute(sa.select(UserRecoveryCode.user_id).distinct())).scalars().all() == [ids["dev"]]


# ── the administrator who restores ──────────────────────────────────────

async def test_the_restoring_administrator_keeps_two_step_the_backup_never_had(sf):
    world = await _world(sf)
    payload = await _export(sf)  # the administrator has nothing in it
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        secret, codes = await turn_on_totp(db, admin)
        device, _ = await add_passkey(db, admin)
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await status.methods(db, admin.id) == ["totp", "passkey"]
        assert await totp.check(db, admin.id, code_at(secret, later), later) is True
        assert await _passkey_works(db, admin.id, device) is True
        assert await recovery_codes.use(db, admin.id, codes[0], NOW) is True


async def test_the_restoring_administrator_is_not_handed_back_an_old_phone(sf):
    """In the backup they had an authenticator and a passkey. Since then they
    replaced the phone. Restoring must not ask for the old one."""
    world = await _world(sf)
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        old_secret, old_codes = await turn_on_totp(db, admin)
        old_device, _ = await add_passkey(db, admin)
        await db.commit()
    payload = await _export(sf)
    async with sf() as db:
        await reset(db, admin.id)
        new_secret, new_codes = await turn_on_totp(db, admin)
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await status.methods(db, admin.id) == ["totp"]
        assert await totp.check(db, admin.id, code_at(old_secret, later), later) is False
        assert await totp.check(db, admin.id, code_at(new_secret, later), later) is True
        assert await _passkey_works(db, admin.id, old_device) is False
        assert await recovery_codes.use(db, admin.id, old_codes[0], NOW) is False
        assert await recovery_codes.use(db, admin.id, new_codes[0], NOW) is True


async def test_the_restoring_administrator_who_has_none_is_not_given_any(sf):
    world = await _world(sf)
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        await turn_on_totp(db, admin)
        await db.commit()
    payload = await _export(sf)
    async with sf() as db:
        await reset(db, admin.id)
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        assert await status.is_on(db, admin.id) is False
        assert (await sign_in.decide(db, admin, NOW))["status"] == "signed_in"


async def test_two_step_follows_an_administrator_the_backup_knows_under_another_id(sf):
    """The server was rebuilt since the backup: same people, new ids."""
    world = await _world(sf)
    payload = await _export(sf)
    old_id = str(uuid.uuid4())
    payload["tables"]["users"].append({
        **copy.deepcopy(payload["tables"]["users"][0]), "id": old_id, "email": "Ops@Example.com",
    })
    # In the backup that account had an authenticator of its own.
    theirs = copy.deepcopy(payload["tables"]["user_two_step"][0])
    payload["tables"]["user_two_step"].append({**theirs, "user_id": old_id})
    async with sf() as db:
        acting_id = await insert(db, User, email="ops@example.com", is_admin=True, is_active=True)
        acting = await db.get(User, acting_id)
        secret, codes = await turn_on_totp(db, acting)
        device, _ = await add_passkey(db, acting)
        await db.commit()

    await _restore(sf, payload, acting_id)

    async with sf() as db:
        account = uuid.UUID(old_id)
        later = NOW + 5 * STEP
        assert await db.get(User, acting_id) is None, "the backup's account is the one that exists"
        assert await status.methods(db, account) == ["totp", "passkey"]
        assert await totp.check(db, account, code_at(secret, later), later) is True
        assert await totp.check(db, account, code_at(world["secret"], later), later) is False
        assert await _passkey_works(db, account, device) is True
        assert await recovery_codes.use(db, account, codes[0], NOW) is True
        # The developer is untouched by all that.
        assert await totp.check(db, world["dev"], code_at(world["secret"], later), later) is True


async def test_a_passkey_the_administrator_holds_now_is_not_given_to_someone_else(sf):
    """The same security key: in the backup it was the developer's; today it
    is registered to the administrator who restores."""
    world = await _world(sf)
    payload = await _export(sf)
    key_in_backup = payload["tables"]["user_passkeys"][0]["credential_id"]
    async with sf() as db:
        await reset(db, world["dev"])
        admin = await db.get(User, world["admin"])
        db.add(UserPasskey(
            user_id=admin.id, credential_id=world["device"].credential_id,
            public_key=b"moved", name="Now the admin's",
        ))
        await db.commit()

    await _restore(sf, payload, world["admin"])

    async with sf() as db:
        held = (await db.execute(sa.select(UserPasskey))).scalars().all()
        assert [(key.user_id, key.name) for key in held] == [(admin.id, "Now the admin's")]
        assert base64.b64encode(held[0].credential_id).decode() == key_in_backup
        # The developer still has the authenticator from the backup.
        assert await status.methods(db, world["dev"]) == ["totp"]


async def test_a_restore_that_fails_changes_no_ones_two_step(sf):
    world = await _world(sf)
    payload = await _export(sf)
    payload["tables"]["user_passkeys"].append({
        **copy.deepcopy(payload["tables"]["user_passkeys"][0]),
        "id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()),  # no such user
    })
    async with sf() as db:
        admin = await db.get(User, world["admin"])
        secret, _ = await turn_on_totp(db, admin)
        await db.commit()

    async with sf() as db:
        with pytest.raises(Exception):
            await apply_configuration(db, payload, SECRET_KEY, acting_user_id=world["admin"])
        await db.rollback()

    async with sf() as db:
        later = NOW + 5 * STEP
        assert await totp.check(db, admin.id, code_at(secret, later), later) is True
        assert await status.methods(db, world["dev"]) == ["totp", "passkey"]
