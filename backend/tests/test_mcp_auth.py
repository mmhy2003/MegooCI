"""authenticate_pat is the single PAT lookup, shared by REST and MCP."""
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from fastapi import HTTPException

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._mcp import seed_member, seed_token
from tests._rbac import build_inmemory_factory

DEV = ["projects.read", "pipelines.manage"]


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def test_valid_full_access_token_returns_owner_with_roles(sf):
    from app.core.deps import authenticate_pat, effective_permissions

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    async with sf() as db:
        user = await authenticate_pat(db, token)
    assert user is not None and user.id == uid
    assert user.active_token_scopes is None
    assert effective_permissions(user) == set(DEV)


async def test_scoped_token_carries_its_scope(sf):
    from app.core.deps import authenticate_pat, effective_permissions

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, scopes=["read.only"])
        await db.commit()
    async with sf() as db:
        user = await authenticate_pat(db, token)
    assert user.active_token_scopes == ["read.only"]
    assert effective_permissions(user) == {"projects.read"}


async def test_authentication_touches_last_used_at(sf):
    from sqlalchemy import select

    from app.core.deps import authenticate_pat
    from app.models.api_token import ApiToken

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid)
        await db.commit()
    async with sf() as db:
        await authenticate_pat(db, token)
    async with sf() as db:
        row = (await db.execute(select(ApiToken))).scalar_one()
    assert row.last_used_at is not None


async def test_unknown_token_is_none(sf):
    from app.core.deps import authenticate_pat

    async with sf() as db:
        assert await authenticate_pat(db, "megci_pat_does_not_exist") is None


async def test_inactive_token_is_none(sf):
    from app.core.deps import authenticate_pat

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, is_active=False)
        await db.commit()
    async with sf() as db:
        assert await authenticate_pat(db, token) is None


async def test_expired_token_is_none(sf):
    from app.core.deps import authenticate_pat

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(
            db, uid, expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
        await db.commit()
    async with sf() as db:
        assert await authenticate_pat(db, token) is None


async def test_unexpired_token_is_accepted(sf):
    from app.core.deps import authenticate_pat

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(
            db, uid, expires_at=datetime.now(timezone.utc) + timedelta(days=1)
        )
        await db.commit()
    async with sf() as db:
        assert (await authenticate_pat(db, token)).id == uid


async def test_get_current_user_still_accepts_a_pat(sf):
    from app.core.deps import get_current_user

    async with sf() as db:
        uid = await seed_member(db, DEV)
        token = await seed_token(db, uid, scopes=["read.only"])
        await db.commit()
    async with sf() as db:
        user = await get_current_user(token=token, db=db)
    assert user.id == uid and user.active_token_scopes == ["read.only"]


async def test_get_current_user_rejects_a_bad_pat(sf):
    from app.core.deps import get_current_user

    async with sf() as db:
        with pytest.raises(HTTPException) as exc:
            await get_current_user(token="megci_pat_nope", db=db)
    assert exc.value.status_code == 401
