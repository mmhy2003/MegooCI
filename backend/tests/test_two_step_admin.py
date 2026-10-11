"""What administrators get: the requirement, the users list, and the reset."""
import os
import sys
import uuid
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

from app import reset_two_step as command
from app.api.v1 import system, users
from app.api.v1.backups import require_global_admin
from app.models.audit import AuditLogEntry
from app.models.user import User
from app.schemas.two_step import SecuritySettings, SecuritySettingsUpdate
from app.schemas.users import UserDetailResponse
from app.services.two_step import recovery_codes, sign_in, status
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, add_passkey, add_user, turn_on_totp

ON = SecuritySettingsUpdate(require_two_step=True)
OFF = SecuritySettingsUpdate(require_two_step=False)


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr("app.core.audit.async_session", factory)
    yield factory
    await engine.dispose()


async def _refused(call) -> HTTPException:
    with pytest.raises(HTTPException) as refused:
        await call
    return refused.value


async def _audited(db) -> list[tuple[str, uuid.UUID | None, uuid.UUID | None, dict | None]]:
    rows = (await db.execute(sa.select(AuditLogEntry))).scalars().all()
    return [(row.action, row.actor_id, row.target_id, row.metadata_json) for row in rows]


def _route(router, path: str, method: str):
    return next(r for r in router.routes if r.path == path and method in r.methods)


# ── the requirement ─────────────────────────────────────────────────────

def test_the_switch_is_for_administrators_of_the_whole_server():
    for method in ("GET", "PUT"):
        calls = [d.call for d in _route(system.router, "/security", method).dependant.dependencies]
        assert require_global_admin in calls, method


async def test_it_starts_off_and_counts_who_has_it(sf):
    async with sf() as db:
        admin, member = await add_user(db, is_admin=True), await add_user(db)
        await add_user(db, is_active=False)
        await turn_on_totp(db, member)

        settings = SecuritySettings(**await system.get_security(db, admin))
        assert settings.model_dump() == {
            "require_two_step": False, "users_total": 2, "users_with_two_step": 1}


async def test_an_administrator_without_it_cannot_require_it(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)

        refused = await _refused(system.set_security(ON, db, admin))

        assert (refused.status_code, refused.detail) == (
            409, "Set up two-step verification on your own account first.")
        assert await status.is_required(db) is False
        assert await _audited(db) == []


@pytest.mark.parametrize("give_method", [turn_on_totp, add_passkey])
async def test_an_administrator_with_it_can_require_it_and_stop(sf, give_method):
    async with sf() as db:
        admin, member = await add_user(db, is_admin=True), await add_user(db)
        await give_method(db, admin)

        turned_on = await system.set_security(ON, db, admin)
        assert turned_on["require_two_step"] is True
        assert (await sign_in.decide(db, member, NOW))["status"] == "two_step_setup_required"
        assert await sign_in.may_renew(db, member.id) is False

        turned_off = await system.set_security(OFF, db, admin)
        assert turned_off["require_two_step"] is False
        assert (await sign_in.decide(db, member, NOW))["status"] == "signed_in"
        assert [(action, actor, meta) for action, actor, _, meta in await _audited(db)] == [
            ("two_step_requirement_changed", admin.id, {"required": True}),
            ("two_step_requirement_changed", admin.id, {"required": False}),
        ]


async def test_saving_the_same_value_records_nothing(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await turn_on_totp(db, admin)
        await system.set_security(ON, db, admin)
        await system.set_security(ON, db, admin)
        await system.set_security(OFF, db, admin)
        await system.set_security(OFF, db, admin)

        assert len(await _audited(db)) == 2


async def test_turning_it_off_needs_no_method(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await status.set_required(db, True)  # as left by another administrator

        assert (await system.set_security(OFF, db, admin))["require_two_step"] is False


# ── the users list ──────────────────────────────────────────────────────

async def test_each_user_is_listed_with_whether_it_is_on(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        with_app, with_key, without = await add_user(db), await add_user(db), await add_user(db)
        await turn_on_totp(db, with_app)
        await add_passkey(db, with_key)
        await db.commit()

        listed = {row["id"]: UserDetailResponse(**row).two_step_enabled
                  for row in await users.list_users(0, 100, db, admin)}

        assert listed == {admin.id: False, with_app.id: True, with_key.id: True, without.id: False}
        assert (await users.get_user(with_app.id, db, admin))["two_step_enabled"] is True
        assert (await users.get_user(without.id, db, admin))["two_step_enabled"] is False


# ── the reset ───────────────────────────────────────────────────────────

def test_the_reset_needs_the_same_permission_as_deleting_a_user():
    def checks(path: str, method: str) -> list[str]:
        route = _route(users.router, path, method)
        return [d.call.__qualname__ for d in route.dependant.dependencies]

    assert checks("/{user_id}/two-step/reset", "POST") == checks("/{user_id}", "DELETE")
    assert "require_permission" in checks("/{user_id}/two-step/reset", "POST")[-1]


async def test_a_reset_clears_the_user_and_is_recorded(sf):
    async with sf() as db:
        admin, member, other = await add_user(db, is_admin=True), await add_user(db), await add_user(db)
        _, codes = await turn_on_totp(db, member)
        await add_passkey(db, member)
        await turn_on_totp(db, other)

        assert await users.reset_user_two_step(member.id, db, admin) is None

        assert await status.is_on(db, member.id) is False
        assert await recovery_codes.use(db, member.id, codes[0], NOW) is False
        assert await status.is_on(db, other.id) is True
        assert await _audited(db) == [
            ("two_step_reset_by_admin", admin.id, member.id, {"was_on": True})]
        # With a password alone they are in again...
        assert (await sign_in.decide(db, member, NOW))["status"] == "signed_in"
        # ...or sent to set it up, where it is required.
        await status.set_required(db, True)
        assert (await sign_in.decide(db, member, NOW))["status"] == "two_step_setup_required"


async def test_resetting_a_user_who_has_nothing_is_harmless_and_still_recorded(sf):
    async with sf() as db:
        admin, member = await add_user(db, is_admin=True), await add_user(db)
        await users.reset_user_two_step(member.id, db, admin)

        assert await _audited(db) == [
            ("two_step_reset_by_admin", admin.id, member.id, {"was_on": False})]


async def test_an_administrator_cannot_reset_themselves_this_way(sf):
    """In Settings, removing a method asks for the password. The reset must
    not be a way for someone at an open session to skip that."""
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await turn_on_totp(db, admin)

        refused = await _refused(users.reset_user_two_step(admin.id, db, admin))

        assert (refused.status_code, refused.detail) == (
            400, "Change your own two-step verification in Settings.")
        assert await status.is_on(db, admin.id) is True
        assert await _audited(db) == []


async def test_resetting_someone_who_does_not_exist(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        refused = await _refused(users.reset_user_two_step(uuid.uuid4(), db, admin))

        assert refused.status_code == 404 and await _audited(db) == []


# ── the server command ──────────────────────────────────────────────────

async def test_the_command_resets_by_email_whatever_its_case(sf):
    async with sf() as db:
        admin = await add_user(db, is_admin=True)
        await turn_on_totp(db, admin)
        await db.commit()

    found, message = await command.reset_by_email(sf, f"  {admin.email.upper()} ")

    assert found is True and admin.email in message and "is reset" in message
    async with sf() as db:
        assert await status.is_on(db, admin.id) is False
        assert (await db.get(User, admin.id)).hashed_password == admin.hashed_password
        assert await _audited(db) == [
            ("two_step_reset_from_server", None, admin.id, {"was_on": True})]


async def test_the_command_says_so_when_there_was_nothing_to_reset(sf):
    async with sf() as db:
        user = await add_user(db)
        await db.commit()

    found, message = await command.reset_by_email(sf, user.email)
    assert found is True and "had no two-step verification" in message


async def test_the_command_does_nothing_for_an_unknown_email(sf):
    async with sf() as db:
        user = await add_user(db)
        await turn_on_totp(db, user)
        await db.commit()

    found, message = await command.reset_by_email(sf, "nobody@example.com")

    assert found is False and "nobody@example.com" in message
    async with sf() as db:
        assert await status.is_on(db, user.id) is True


@pytest.mark.parametrize("argv", [[], ["a@example.com", "b@example.com"], [""], ["--help"]])
def test_the_command_wants_exactly_one_email(argv, capsys):
    assert command.main(argv) == 2
    assert "usage: python -m app.reset_two_step <email>" in capsys.readouterr().err
