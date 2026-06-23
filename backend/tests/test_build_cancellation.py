"""Cancellation: flag helpers, executor stop-advancing, gate bail-out.

Runs the real services against in-memory SQLite using the same @compiles
shims as the other dispatcher/executor tests.
"""
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.dialects.postgresql import ARRAY, JSON as PG_JSON, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._concurrency import create_concurrency_indexes


@compiles(UUID, "sqlite")
def _uuid_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "CHAR(32)"


@compiles(JSONB, "sqlite")
@compiles(PG_JSON, "sqlite")
def _json_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


@compiles(ARRAY, "sqlite")
def _array_sqlite(element, compiler, **kw):  # pragma: no cover - DDL glue
    return "JSON"


class FakeAsyncRedis:
    """Minimal dict-backed stand-in for redis.asyncio used in cancel tests."""
    def __init__(self, store=None):
        self.store = dict(store or {})

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)

    async def publish(self, *a, **k):
        return 0

    async def aclose(self):
        pass


@pytest_asyncio.fixture
async def session_factory():
    from app.models.agent import Agent
    from app.models.build import Build, Stage, Step

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        for m in (Agent, Build, Stage, Step):
            await conn.run_sync(lambda c, m=m: m.__table__.create(c))
        await create_concurrency_indexes(conn)
    yield async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    await engine.dispose()


async def test_set_and_read_cancel_flag():
    from app.services.agent_dispatcher import (
        build_cancel_flag_key, set_build_cancel_flag, build_cancel_requested,
    )
    redis = FakeAsyncRedis()
    bid = uuid.uuid4()

    assert await build_cancel_requested(redis, bid) is False
    await set_build_cancel_flag(redis, bid)
    assert redis.store[build_cancel_flag_key(bid)] == "1"
    assert await build_cancel_requested(redis, bid) is True


async def test_signal_build_cancel_sets_flag_and_notifies(session_factory, monkeypatch):
    from app.services import agent_dispatcher
    from app.services.agent_dispatcher import (
        build_cancel_flag_key, signal_build_cancel,
    )

    notified = []

    async def _spy_notify(db, build_id):
        notified.append(build_id)

    monkeypatch.setattr(agent_dispatcher, "notify_agents_of_cancel", _spy_notify)

    redis = FakeAsyncRedis()
    bid = uuid.uuid4()
    async with session_factory() as db:
        await signal_build_cancel(db, bid, redis)

    assert redis.store[build_cancel_flag_key(bid)] == "1"
    assert notified == [bid]
