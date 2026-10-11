"""The login page asks, before anyone is signed in, whether to offer
"Create an account". The answer follows the rule sign-up itself applies."""
import os
import sys
import uuid
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

# litellm is not installed in the test venv; the API router imports a module that needs it.
if "litellm" not in sys.modules:
    sys.modules["litellm"] = MagicMock()
    sys.modules["litellm.exceptions"] = MagicMock()

from app.api.v1 import auth
from app.config import get_settings
from app.core.deps import get_current_active_user, get_current_user, oauth2_scheme
from app.models.user import User
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def signup_setting(monkeypatch):
    def set_to(enabled: bool) -> None:
        monkeypatch.setattr(get_settings(), "MEGOOCI_SIGNUP_ENABLED", enabled)

    return set_to


async def _add_user(sf) -> None:
    async with sf() as db:
        db.add(User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="Someone"))
        await db.commit()


async def test_open_when_signup_is_enabled(sf, signup_setting):
    signup_setting(True)
    await _add_user(sf)

    async with sf() as db:
        assert await auth.signup_status(db) == {"signup_enabled": True}


async def test_closed_when_signup_is_disabled(sf, signup_setting):
    signup_setting(False)
    await _add_user(sf)

    async with sf() as db:
        assert await auth.signup_status(db) == {"signup_enabled": False}


async def test_open_on_a_server_without_users_even_when_disabled(sf, signup_setting):
    # The first account can always be created; it becomes the administrator.
    signup_setting(False)

    async with sf() as db:
        assert await auth.signup_status(db) == {"signup_enabled": True}


def _route(path: str):
    return next(route for route in auth.router.routes if route.path == path)


def _calls(dependant) -> set:
    found = set()
    for dependency in dependant.dependencies:
        found.add(dependency.call)
        found |= _calls(dependency)
    return found


def test_it_needs_no_sign_in():
    calls = _calls(_route("/signup-status").dependant)

    assert not calls & {get_current_user, get_current_active_user, oauth2_scheme}
    # The check itself can tell a public route from a protected one.
    assert get_current_user in _calls(_route("/me").dependant)


def test_it_is_mounted_with_the_other_auth_routes():
    from app.api.v1.router import api_v1_router

    assert "/auth/signup-status" in {route.path for route in api_v1_router.routes}


def test_it_says_nothing_else_about_the_server():
    assert set(auth.SignupStatus.model_fields) == {"signup_enabled"}
