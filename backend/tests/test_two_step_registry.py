"""``docker login``: the password alone is not enough for an account that uses
two-step verification, and an API token works in its place."""
import base64
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import registry_oci
from app.core import registry_auth
from app.core.registry_auth import RegistryLoginRefused, authenticate_basic, decode_registry_token
from app.models.registry import RegistryDeployToken
from app.models.user import User
from app.services.two_step import status
from tests._mcp import seed_member, seed_token
from tests._rbac import build_inmemory_factory
from tests._two_step import PASSWORD, add_passkey, add_user, cheap_hash, turn_on_totp

REFUSED = (None, None, [])
TWO_STEP = (
    "This account uses two-step verification. Use an API token as the password "
    "(Settings → API tokens)."
)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _member(db, permissions, *, password=PASSWORD, is_admin=False) -> User:
    user = await db.get(User, await seed_member(db, permissions, is_admin=is_admin))
    user.hashed_password = cheap_hash(password)
    await db.flush()
    return user


async def _message(call) -> str:
    with pytest.raises(RegistryLoginRefused) as refused:
        await call
    return str(refused.value)


# ── the account password ────────────────────────────────────────────────

async def test_the_password_still_works_for_an_account_without_two_step(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])

        assert await authenticate_basic(db, user.email, PASSWORD) == (
            str(user.id), user.id, ["pull", "push"])


@pytest.mark.parametrize("give_method", [turn_on_totp, add_passkey])
async def test_the_password_is_refused_once_the_account_has_a_method(sf, give_method):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        await give_method(db, user)

        assert await _message(authenticate_basic(db, user.email, PASSWORD)) == TWO_STEP
        # By name as well as by email.
        assert await _message(authenticate_basic(db, user.name, PASSWORD)) == TWO_STEP


async def test_the_password_is_refused_when_it_is_required_and_not_set_up(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        await status.set_required(db, True)

        assert await _message(authenticate_basic(db, user.email, PASSWORD)) == TWO_STEP


async def test_a_wrong_password_learns_nothing_about_the_account(sf):
    async with sf() as db:
        with_two_step = await _member(db, ["registry.read"])
        await turn_on_totp(db, with_two_step)
        without = await _member(db, ["registry.read"])

        for user in (with_two_step, without):
            assert await authenticate_basic(db, user.email, "wrong") == REFUSED
        assert await authenticate_basic(db, "nobody@example.com", PASSWORD) == REFUSED


async def test_an_account_without_a_password_is_refused_not_a_crash(sf):
    async with sf() as db:
        user = await add_user(db, password=None)
        assert await authenticate_basic(db, user.email, "anything") == REFUSED


async def test_a_deactivated_account_is_refused_as_before(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        user.is_active = False
        await db.flush()

        assert await authenticate_basic(db, user.email, PASSWORD) == REFUSED


# ── a deploy token ──────────────────────────────────────────────────────

async def test_a_deploy_token_works_as_before_even_when_two_step_is_required(sf):
    async with sf() as db:
        owner = await add_user(db)
        token = RegistryDeployToken(
            id=uuid.uuid4(), name="ci", token_hash=cheap_hash("deploy-secret"),
            token_hint="deploy", scope="push", is_active=True, created_by=owner.id,
        )
        db.add(token)
        await status.set_required(db, True)
        await db.flush()

        assert await authenticate_basic(db, "deploy-token", "deploy-secret") == (
            f"deploy-token:{token.id}", None, ["pull", "push"])
        assert await authenticate_basic(db, "deploy-token", "wrong") == REFUSED


# ── an API token as the password ────────────────────────────────────────

async def test_a_full_access_token_stands_for_the_account(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        token = await seed_token(db, user.id)

        assert await authenticate_basic(db, user.email, token) == (
            str(user.id), user.id, ["pull", "push"])


async def test_the_token_says_who_it_is_whatever_the_username(sf):
    async with sf() as db:
        owner = await _member(db, ["registry.read"])
        other = await _member(db, ["registry.read", "registry.push", "registry.manage"])
        token = await seed_token(db, owner.id)

        for username in ("", "anything", other.email):
            assert await authenticate_basic(db, username, token) == (
                str(owner.id), owner.id, ["pull"])


@pytest.mark.parametrize("give_method", [turn_on_totp, add_passkey])
async def test_a_token_works_for_an_account_with_two_step(sf, give_method):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        await give_method(db, user)
        await status.set_required(db, True)
        token = await seed_token(db, user.id)

        assert await authenticate_basic(db, user.email, token) == (
            str(user.id), user.id, ["pull", "push"])


@pytest.mark.parametrize("scopes, actions", [
    (["read.only"], ["pull"]),
    (["container.registry"], ["pull", "push"]),
    (["container.registry", "coding.agent"], ["pull", "push"]),
])
async def test_a_scoped_token_may_do_what_its_scope_says(sf, scopes, actions):
    async with sf() as db:
        admin = await _member(db, [], is_admin=True)
        token = await seed_token(db, admin.id, scopes=scopes)

        assert await authenticate_basic(db, admin.email, token) == (str(admin.id), admin.id, actions)


async def test_no_scope_lets_a_token_delete_images(sf):
    async with sf() as db:
        admin = await _member(db, [], is_admin=True)
        every_scope = ["artifacts.download", "automate.workflows", "read.only", "coding.agent",
                       "container.registry"]
        token = await seed_token(db, admin.id, scopes=every_scope)

        _, _, actions = await authenticate_basic(db, admin.email, token)
        assert actions == ["pull", "push"]


async def test_a_scope_never_gives_more_than_the_account_has(sf):
    async with sf() as db:
        reader = await _member(db, ["registry.read"])
        token = await seed_token(db, reader.id, scopes=["container.registry"])

        assert await authenticate_basic(db, reader.email, token) == (str(reader.id), reader.id, ["pull"])


def test_the_registry_scope_is_pull_and_push():
    from app.core.token_scopes import TOKEN_SCOPES

    assert TOKEN_SCOPES["container.registry"]["permissions"] == {"registry.read", "registry.push"}


@pytest.mark.parametrize("scopes", [["coding.agent"], ["artifacts.download"], ["automate.workflows"]])
async def test_a_token_scoped_to_something_else_is_refused_with_the_reason(sf, scopes):
    async with sf() as db:
        admin = await _member(db, [], is_admin=True)
        token = await seed_token(db, admin.id, scopes=scopes)

        message = await _message(authenticate_basic(db, admin.email, token))
        assert message == "This API token's scope does not include the registry."


async def test_tokens_that_are_not_valid_are_refused_like_a_wrong_password(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        expired = await seed_token(db, user.id, expires_at=yesterday)
        revoked = await seed_token(db, user.id, is_active=False)

        for token in (expired, revoked, "megci_pat_" + "x" * 44):
            assert await authenticate_basic(db, user.email, token) == REFUSED


async def test_a_token_of_a_deactivated_account_is_refused(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        token = await seed_token(db, user.id)
        user.is_active = False
        await db.flush()

        assert await authenticate_basic(db, user.email, token) == REFUSED


# ── the token endpoint, as docker calls it ──────────────────────────────

def _docker_asks(username: str, password: str) -> Request:
    basic = base64.b64encode(f"{username}:{password}".encode()).decode()
    return Request({
        "type": "http", "method": "GET", "path": "/v2/token", "query_string": b"",
        "headers": [(b"authorization", f"Basic {basic}".encode())],
    })


async def test_docker_is_told_what_to_do_instead(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        await turn_on_totp(db, user)

        response = await registry_oci.get_token(_docker_asks(user.email, PASSWORD), "", "", db)

        assert isinstance(response, JSONResponse) and response.status_code == 401
        body = json.loads(response.body)
        # Both shapes the docker client reads a message from.
        assert body["details"] == TWO_STEP
        assert body["errors"] == [{"code": "UNAUTHORIZED", "message": TWO_STEP}]


async def test_docker_gets_a_registry_token_for_an_api_token(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read", "registry.push"])
        await turn_on_totp(db, user)
        token = await seed_token(db, user.id)

        response = await registry_oci.get_token(_docker_asks(user.email, token), "", "", db)

        granted = decode_registry_token(response["token"])
        assert granted["sub"] == str(user.id) and granted["access"] == ["pull", "push"]


async def test_docker_gets_the_usual_refusal_for_a_wrong_password(sf):
    async with sf() as db:
        user = await _member(db, ["registry.read"])
        await turn_on_totp(db, user)

        with pytest.raises(HTTPException) as refused:
            await registry_oci.get_token(_docker_asks(user.email, "wrong"), "", "", db)
        assert (refused.value.status_code, refused.value.detail) == (401, "Invalid credentials")


def test_the_messages_are_the_ones_the_design_gives():
    assert registry_auth.TWO_STEP_MESSAGE == TWO_STEP
