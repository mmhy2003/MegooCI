"""Two-step verification over real HTTP requests: what a browser sends and
gets back, with the routers mounted where the application mounts them."""
import os
import sys
from unittest.mock import MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; some routers import a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import auth, system, two_step, two_step_account, users
from app.database import get_db
from tests._mcp import seed_token
from tests._passkey import SoftwareAuthenticator
from tests._rbac import build_inmemory_factory
from tests._two_step import NOW, PASSWORD, add_user, code_at

API = "/api/v1"


class FakeRedis:
    """Counts requests like the rate limiter's Redis would."""

    counts: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        return None

    async def ttl(self, key: str) -> int:
        return 42

    async def aclose(self) -> None:
        return None


@pytest_asyncio.fixture
async def sf(monkeypatch):
    engine, factory = await build_inmemory_factory()
    monkeypatch.setattr("app.core.audit.async_session", factory)
    monkeypatch.setattr("app.services.two_step.clock.now", lambda: NOW)
    FakeRedis.counts = {}
    monkeypatch.setattr("app.core.rate_limit.aioredis.from_url", lambda *a, **k: FakeRedis())
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def http(sf):
    """A client for an app with the routers two-step verification touches,
    mounted as ``app/api/v1/router.py`` mounts them."""
    api = APIRouter()
    api.include_router(auth.router, prefix="/auth")
    api.include_router(two_step.router, prefix="/auth/two-step")
    api.include_router(two_step_account.router, prefix="/auth/two-step")
    api.include_router(system.router, prefix="/system")
    api.include_router(users.router, prefix="/users")
    app = FastAPI()
    app.include_router(api, prefix=API)

    async def _get_db():
        async with sf() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _get_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


async def _account(sf, **kwargs):
    async with sf() as db:
        user = await add_user(db, **kwargs)
        await db.commit()
    return user


def _as(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _sign_in(http, user) -> dict:
    """Sign in an account that has no second step yet. Returns its headers."""
    response = await http.post(f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return _as(response.json()["access_token"])


# ── a whole sign-in ─────────────────────────────────────────────────────

async def test_setting_up_an_authenticator_then_signing_in_with_it(sf, http):
    user = await _account(sf)
    session = await _sign_in(http, user)

    # Settings: scan, confirm, save the codes.
    setup = (await http.post(f"{API}/auth/two-step/totp", headers=session)).json()
    confirmed = await http.post(
        f"{API}/auth/two-step/totp/confirm", headers=session,
        json={"code": code_at(setup["secret"], NOW), "password": PASSWORD})
    assert confirmed.status_code == 200 and len(confirmed.json()["recovery_codes"]) == 10
    summary = (await http.get(f"{API}/auth/two-step", headers=session)).json()
    assert summary["enabled"] is True and summary["recovery_codes"]["left"] == 10

    # The next sign-in: the password is no longer enough.
    password_step = await http.post(
        f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})
    assert password_step.status_code == 200
    body = password_step.json()
    assert set(body) == {"status", "ticket", "methods", "passkey_origin"}
    assert body["status"] == "two_step_required" and body["methods"] == ["totp"]

    # The confirming code is spent; a recovery code gets in and says what is left.
    spent = await http.post(
        f"{API}/auth/two-step/verify",
        json={"ticket": body["ticket"], "code": code_at(setup["secret"], NOW)})
    assert spent.status_code == 401 and spent.json() == {"detail": "That code is not right."}
    signed_in = await http.post(
        f"{API}/auth/two-step/verify",
        json={"ticket": body["ticket"], "recovery_code": confirmed.json()["recovery_codes"][0]})
    assert signed_in.status_code == 200
    assert set(signed_in.json()) == {
        "status", "access_token", "refresh_token", "token_type", "recovery_codes_left"}
    assert signed_in.json()["recovery_codes_left"] == 9

    me = (await http.get(f"{API}/auth/me", headers=_as(signed_in.json()["access_token"]))).json()
    assert me["email"] == user.email
    assert (me["two_step_enabled"], me["two_step_setup_required"]) == (True, False)


async def test_a_plain_sign_in_looks_as_it_always_did_plus_a_status(sf, http):
    user = await _account(sf)
    response = await http.post(f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})

    assert set(response.json()) == {"status", "access_token", "refresh_token", "token_type"}
    assert response.json()["status"] == "signed_in" and response.json()["token_type"] == "bearer"


async def test_a_passkey_added_in_settings_signs_in(sf, http):
    user = await _account(sf)
    session = await _sign_in(http, user)
    device = SoftwareAuthenticator()

    offered = (await http.post(f"{API}/auth/two-step/passkeys/options", headers=session)).json()
    added = await http.post(
        f"{API}/auth/two-step/passkeys", headers=session,
        json={"ticket": offered["ticket"], "credential": device.create(offered["options"]),
              "name": "Work laptop", "password": PASSWORD})
    assert added.status_code == 200 and added.json()["passkey"]["name"] == "Work laptop"
    passkey_id = added.json()["passkey"]["id"]
    renamed = await http.patch(
        f"{API}/auth/two-step/passkeys/{passkey_id}", headers=session, json={"name": "Desk"})
    assert renamed.json()["name"] == "Desk"

    ticket = (await http.post(
        f"{API}/auth/login", json={"email": user.email, "password": PASSWORD})).json()["ticket"]
    options = (await http.post(
        f"{API}/auth/two-step/passkey/options", json={"ticket": ticket})).json()["options"]
    signed_in = await http.post(
        f"{API}/auth/two-step/passkey/verify",
        json={"ticket": ticket, "credential": device.get(options)})
    assert signed_in.status_code == 200 and signed_in.json()["status"] == "signed_in"

    # Removing it needs the password, and turns two-step verification off.
    wrong = await http.post(
        f"{API}/auth/two-step/passkeys/{passkey_id}/remove", headers=session,
        json={"password": "not it"})
    assert wrong.status_code == 400 and wrong.json() == {"detail": "Wrong password."}
    removed = await http.post(
        f"{API}/auth/two-step/passkeys/{passkey_id}/remove", headers=session,
        json={"password": PASSWORD})
    assert removed.status_code == 200 and removed.json()["enabled"] is False


# ── when it is required ─────────────────────────────────────────────────

async def test_an_administrator_requires_it_and_a_user_sets_it_up_to_get_in(sf, http):
    admin, member = await _account(sf, is_admin=True), await _account(sf)
    admin_session = await _sign_in(http, admin)
    member_session = await _sign_in(http, member)
    member_refresh = (await http.post(
        f"{API}/auth/login", json={"email": member.email, "password": PASSWORD})).json()["refresh_token"]

    # Not before the administrator has it on.
    early = await http.put(f"{API}/system/security", headers=admin_session,
                           json={"require_two_step": True})
    assert early.status_code == 409
    setup = (await http.post(f"{API}/auth/two-step/totp", headers=admin_session)).json()
    await http.post(f"{API}/auth/two-step/totp/confirm", headers=admin_session,
                    json={"code": code_at(setup["secret"], NOW), "password": PASSWORD})
    turned_on = await http.put(f"{API}/system/security", headers=admin_session,
                               json={"require_two_step": True})
    assert turned_on.json() == {"require_two_step": True, "users_total": 2, "users_with_two_step": 1}

    # The member's open session is told, and can no longer renew itself.
    me = (await http.get(f"{API}/auth/me", headers=member_session)).json()
    assert me["two_step_setup_required"] is True
    renewal = await http.post(f"{API}/auth/refresh", json={"refresh_token": member_refresh})
    assert renewal.status_code == 401

    # Signing in again leads through the setup; only its end gives a session.
    password_step = (await http.post(
        f"{API}/auth/login", json={"email": member.email, "password": PASSWORD})).json()
    assert set(password_step) == {"status", "ticket", "passkey_origin"}
    assert password_step["status"] == "two_step_setup_required"
    ticket = {"ticket": password_step["ticket"]}
    assert (await http.post(f"{API}/auth/two-step/setup/finish", json=ticket)).status_code == 410
    member_setup = (await http.post(f"{API}/auth/two-step/setup/totp", json=ticket)).json()
    codes = await http.post(
        f"{API}/auth/two-step/setup/totp/confirm",
        json={**ticket, "code": code_at(member_setup["secret"], NOW)})
    assert len(codes.json()["recovery_codes"]) == 10
    finished = await http.post(f"{API}/auth/two-step/setup/finish", json=ticket)
    assert finished.status_code == 200 and finished.json()["status"] == "signed_in"

    # The administrator sees it, and can reset the member, but not themselves.
    listed = (await http.get(f"{API}/users/", headers=admin_session)).json()
    assert {row["email"]: row["two_step_enabled"] for row in listed} == {
        admin.email: True, member.email: True}
    reset = await http.post(f"{API}/users/{member.id}/two-step/reset", headers=admin_session)
    assert reset.status_code == 204 and reset.content == b""
    own = await http.post(f"{API}/users/{admin.id}/two-step/reset", headers=admin_session)
    assert own.status_code == 400
    # A member cannot touch the switch or the reset.
    new_session = _as(finished.json()["access_token"])
    assert (await http.get(f"{API}/system/security", headers=new_session)).status_code == 403
    assert (await http.post(
        f"{API}/users/{admin.id}/two-step/reset", headers=new_session)).status_code == 403


# ── who may call what ───────────────────────────────────────────────────

async def test_the_settings_routes_need_a_session_and_refuse_an_api_token(sf, http):
    user = await _account(sf, is_admin=True)
    async with sf() as db:
        token = await seed_token(db, user.id)
        await db.commit()

    assert (await http.get(f"{API}/auth/two-step")).status_code == 401
    assert (await http.post(f"{API}/auth/two-step/totp")).status_code == 401
    for method, path in (("GET", ""), ("POST", "/totp"), ("POST", "/passkeys/options")):
        refused = await http.request(method, f"{API}/auth/two-step{path}", headers=_as(token))
        assert refused.status_code == 403, path
        assert "API token" in refused.json()["detail"]
    # The token still works where tokens are meant to.
    assert (await http.get(f"{API}/auth/me", headers=_as(token))).status_code == 200


@pytest.mark.parametrize("path, body", [
    ("/verify", {"ticket": "nonsense", "code": "123456"}),
    ("/passkey/options", {"ticket": "nonsense"}),
    ("/passkey/verify", {"ticket": "nonsense", "credential": {}}),
    ("/setup/totp", {"ticket": "nonsense"}),
    ("/setup/totp/confirm", {"ticket": "nonsense", "code": "123456"}),
    ("/setup/passkey/options", {"ticket": "nonsense"}),
    ("/setup/passkey", {"ticket": "nonsense", "credential": {}}),
    ("/setup/finish", {"ticket": "nonsense"}),
])
async def test_without_a_real_ticket_every_sign_in_route_says_gone(sf, http, path, body):
    response = await http.post(f"{API}/auth/two-step{path}", json=body)

    assert response.status_code == 410
    assert response.json() == {"detail": "That took too long. Sign in again."}


@pytest.mark.parametrize("body", [
    {}, {"code": "123456"}, {"ticket": "x" * 201, "code": "1"}, {"ticket": "t", "code": "9" * 65},
    {"ticket": ["a"]}, "not an object",
])
async def test_malformed_requests_are_rejected_before_anything_is_looked_up(sf, http, body):
    response = await http.post(f"{API}/auth/two-step/verify", json=body)
    assert response.status_code == 422


async def test_the_sign_in_routes_are_rate_limited_per_address(sf, http):
    statuses = [
        (await http.post(f"{API}/auth/two-step/verify",
                         json={"ticket": "nonsense", "code": "123456"})).status_code
        for _ in range(31)
    ]

    assert statuses[:30] == [410] * 30 and statuses[30] == 429


async def test_the_password_prompts_are_rate_limited_too(sf, http):
    user = await _account(sf)
    session = await _sign_in(http, user)

    statuses = [
        (await http.post(f"{API}/auth/two-step/recovery-codes", headers=session,
                         json={"password": "guess"})).status_code
        for _ in range(11)
    ]

    assert statuses[:10] == [400] * 10 and statuses[10] == 429
