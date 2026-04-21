"""
Gate resolution endpoints for wait_webhook and wait_input step types.

These endpoints allow external systems (webhooks) and users (approvals) to
resume a paused pipeline step by writing a payload to the Redis key that
the corresponding wait handler is polling.

Webhook gates require a per-step gate token passed via the
``X-Gate-Token`` header or ``gate_token`` query parameter.  The token is
generated when the wait_webhook step begins executing and stored in Redis
at ``gate:token:{step_id}``.
"""

import json
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import require_permission
from app.database import get_db
from app.models.build import Step
from app.models.user import User

router = APIRouter()


class WebhookGatePayload(BaseModel):
    """Arbitrary key-value payload from the external webhook."""

    event: str | None = None
    data: dict | None = None


class InputGatePayload(BaseModel):
    approved: bool


def _gate_key(step_id: str, gate_type: str) -> str:
    return f"gate:{gate_type}:{step_id}"


def _gate_token_key(step_id: str) -> str:
    return f"gate:token:{step_id}"


async def _verify_gate_token(step_id: uuid.UUID, provided_token: str | None) -> None:
    """Check the caller-supplied gate token against the one stored in Redis."""
    if not provided_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing gate token. Supply via X-Gate-Token header or gate_token query parameter.",
        )
    settings = get_settings()
    redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        stored_token = await redis_client.get(_gate_token_key(str(step_id)))
    finally:
        await redis_client.aclose()

    if stored_token is None or stored_token != provided_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid gate token",
        )


@router.post("/webhook/{step_id}", status_code=status.HTTP_202_ACCEPTED)
async def resolve_webhook_gate(
    step_id: uuid.UUID,
    body: WebhookGatePayload,
    db: AsyncSession = Depends(get_db),
    x_gate_token: str | None = Header(None),
    gate_token: str | None = Query(None),
) -> dict:
    """Called by an external system to unblock a ``wait_webhook`` step.

    Requires a per-step gate token for authentication (via ``X-Gate-Token``
    header or ``gate_token`` query parameter).
    """
    await _verify_gate_token(step_id, x_gate_token or gate_token)

    step = await db.get(Step, step_id)
    if step is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if step.step_type != "wait_webhook":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step is not a wait_webhook gate")
    if step.status != "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step is not currently waiting")

    settings = get_settings()
    redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        payload = body.model_dump()
        await redis_client.set(
            _gate_key(str(step_id), "webhook"),
            json.dumps(payload),
            ex=7200,
        )
    finally:
        await redis_client.aclose()

    return {"status": "accepted", "step_id": str(step_id)}


@router.post("/input/{step_id}", status_code=status.HTTP_202_ACCEPTED)
async def resolve_input_gate(
    step_id: uuid.UUID,
    body: InputGatePayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission("builds.manage")),
) -> dict:
    """Called by the UI when a user approves or rejects a ``wait_input`` step."""
    step = await db.get(Step, step_id)
    if step is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Step not found")
    if step.step_type != "wait_input":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step is not a wait_input gate")
    if step.status != "running":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Step is not currently waiting")

    settings = get_settings()
    redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
    try:
        payload = {
            "approved": body.approved,
            "user": current_user.username if hasattr(current_user, "username") else str(current_user.id),
        }
        await redis_client.set(
            _gate_key(str(step_id), "input"),
            json.dumps(payload),
            ex=7200,
        )
    finally:
        await redis_client.aclose()

    action = "approved" if body.approved else "rejected"
    return {"status": action, "step_id": str(step_id)}
