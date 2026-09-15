"""Browser-facing pub/sub WebSocket relay must release Redis when the client leaves.

Regression: handlers parked in ``pubsub.listen()`` only noticed a disconnect on
their next ``send_text``. On a quiet channel (finished build's logs, no
notifications, all events filtered for a scoped user) that never happens, so
each abandoned socket leaked a Redis connection until the backend hit
``OSError: [Errno 24] Too many open files``.
"""

import asyncio

from redis.exceptions import ConnectionError as RedisConnectionError
from starlette import status

import app.api.v1.websocket as ws_mod


class FakeWebSocket:
    def __init__(self):
        self.sent: list[str] = []
        self.closed_code: int | None = None
        self._gone = asyncio.Event()

    async def receive(self) -> dict:
        await self._gone.wait()
        return {"type": "websocket.disconnect", "code": 1001}

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed_code = code

    def disconnect(self) -> None:
        self._gone.set()


class FakePubSub:
    def __init__(self, messages=(), fail_subscribe: bool = False):
        self._messages = list(messages)
        self._fail_subscribe = fail_subscribe
        self.closed = False

    async def subscribe(self, *channels: str) -> None:
        if self._fail_subscribe:
            raise RedisConnectionError("Error 24 connecting to redis:6379. Too many open files.")

    async def listen(self):
        for message in self._messages:
            yield message
        # Quiet channel: nothing more is ever published.
        await asyncio.Event().wait()

    async def aclose(self) -> None:
        self.closed = True


class FakeRedis:
    def __init__(self, pubsub: FakePubSub):
        self._pubsub = pubsub
        self.closed = False

    def pubsub(self) -> FakePubSub:
        return self._pubsub

    async def aclose(self) -> None:
        self.closed = True


def _patch_redis(monkeypatch, pubsub: FakePubSub) -> FakeRedis:
    fake = FakeRedis(pubsub)
    monkeypatch.setattr(ws_mod.aioredis, "from_url", lambda *a, **k: fake)
    return fake


async def test_relay_releases_redis_when_client_disconnects_on_quiet_channel(monkeypatch):
    pubsub = FakePubSub()
    redis_client = _patch_redis(monkeypatch, pubsub)
    websocket = FakeWebSocket()

    relay = asyncio.create_task(ws_mod._relay_pubsub(websocket, "build:x:logs"))
    await asyncio.sleep(0.01)
    websocket.disconnect()

    await asyncio.wait_for(relay, timeout=1)
    assert pubsub.closed
    assert redis_client.closed


async def test_relay_forwards_transformed_messages(monkeypatch):
    pubsub = FakePubSub(
        [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": "keep"},
            {"type": "message", "data": "drop"},
        ]
    )
    _patch_redis(monkeypatch, pubsub)
    websocket = FakeWebSocket()

    relay = asyncio.create_task(
        ws_mod._relay_pubsub(
            websocket, "builds:updates", lambda data: None if data == "drop" else data
        )
    )
    await asyncio.sleep(0.01)
    websocket.disconnect()
    await asyncio.wait_for(relay, timeout=1)

    assert websocket.sent == ["keep"]
    assert pubsub.closed


async def test_relay_closes_socket_cleanly_when_redis_unavailable(monkeypatch):
    pubsub = FakePubSub(fail_subscribe=True)
    redis_client = _patch_redis(monkeypatch, pubsub)
    websocket = FakeWebSocket()

    await asyncio.wait_for(ws_mod._relay_pubsub(websocket, "builds:updates"), timeout=1)

    assert websocket.closed_code == status.WS_1011_INTERNAL_ERROR
    assert pubsub.closed
    assert redis_client.closed
