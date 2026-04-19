import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import get_settings

router = APIRouter()


@router.websocket("/ws/builds/{build_id}/logs")
async def build_logs_ws(websocket: WebSocket, build_id: uuid.UUID) -> None:
    await websocket.accept()
    settings = get_settings()

    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    channel_name = f"build:{build_id}:logs"

    pubsub = redis_client.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"])
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()
        await redis_client.aclose()
