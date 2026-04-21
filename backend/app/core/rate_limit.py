"""Simple Redis-backed sliding-window rate limiter for FastAPI.

Usage as a dependency::

    @router.post("/login")
    async def login(
        ...,
        _rl: None = Depends(rate_limit("login", max_requests=10, window_seconds=60)),
    ) -> dict:
        ...
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import redis.asyncio as aioredis
from fastapi import Depends, HTTPException, Request, status

from app.config import get_settings

logger = logging.getLogger(__name__)


def rate_limit(
    bucket: str,
    max_requests: int = 10,
    window_seconds: int = 60,
) -> Callable:
    """Return a FastAPI dependency that enforces a per-IP rate limit.

    *bucket* differentiates separate rate-limit counters (e.g. ``"login"``,
    ``"signup"``).
    """

    async def _check(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        key = f"ratelimit:{bucket}:{client_ip}"

        settings = get_settings()
        redis_client = aioredis.from_url(
            settings.MEGOOCI_REDIS_URL, decode_responses=True,
        )
        try:
            current = await redis_client.incr(key)
            if current == 1:
                await redis_client.expire(key, window_seconds)
            if current > max_requests:
                ttl = await redis_client.ttl(key)
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Too many requests. Try again in {max(ttl, 1)} seconds.",
                    headers={"Retry-After": str(max(ttl, 1))},
                )
        except HTTPException:
            raise
        except Exception:
            logger.warning("Rate limit check failed (Redis unavailable?)", exc_info=True)
        finally:
            await redis_client.aclose()

    return _check
