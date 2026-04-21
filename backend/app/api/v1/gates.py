"""
Gate resolution endpoints for wait_webhook and wait_input step types.

These endpoints allow external systems (webhooks) and users (approvals) to
resume a paused pipeline step by writing a payload to the Redis key that
the corresponding wait handler is polling.
"""

import json
import uuid

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import get_current_active_user
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


@router.post("/webhook/{step_id}", status_code=status.HTTP_202_ACCEPTED)
async def resolve_webhook_gate(
    step_id: uuid.UUID,
    body: WebhookGatePayload,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Called by an external system to unblock a ``wait_webhook`` step."""
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
    current_user: User = Depends(get_current_active_user),
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
