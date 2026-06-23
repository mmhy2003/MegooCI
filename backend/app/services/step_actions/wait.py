"""
Handlers for pipeline-gate step types that pause execution:
  - wait_webhook  — wait until an external webhook callback is received
  - wait_input    — wait until a user manually approves/rejects

Both work by polling a Redis key that gets set by an external API endpoint
(the webhook receiver or the approval API). The executor yields periodic
"waiting…" log lines so the build doesn't look stuck.

YAML examples:

  - wait_webhook:
      name: "deployment-callback"
      timeout: 3600
      match:
        event: deployment_complete

  - wait_input:
      prompt: "Deploy to production?"
      timeout: 86400
      allowed_users:
        - admin
"""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any, AsyncIterator

import redis.asyncio as aioredis
from sqlalchemy import func as sa_func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.user import User
from app.services.in_app_notifications import (
    get_admin_user_ids,
    notify_users,
)
from app.services.agent_dispatcher import build_cancel_requested

from . import register
from .base import LogLine, StepActionHandler, StepContext, StepResult

_DEFAULT_WEBHOOK_TIMEOUT = 3600
_DEFAULT_INPUT_TIMEOUT = 86400
_POLL_INTERVAL = 2


def _gate_key(step_id: str, gate_type: str) -> str:
    """Redis key where the resolution payload is written."""
    return f"gate:{gate_type}:{step_id}"


def _gate_token_key(step_id: str) -> str:
    """Redis key where the per-step gate auth token is stored."""
    return f"gate:token:{step_id}"


async def _resolve_approver_user_ids(
    db: AsyncSession,
    allowed_users: list[str],
) -> list:
    """Resolve allowed_users names/emails to user IDs.

    Falls back to all admin users if the list is empty or no matches found.
    """
    if not allowed_users:
        return await get_admin_user_ids(db)

    # Match by name or email (case-insensitive)
    lower_names = [u.lower() for u in allowed_users]
    result = await db.execute(
        select(User.id).where(
            User.is_active.is_(True),
            or_(
                sa_func.lower(User.name).in_(lower_names),
                sa_func.lower(User.email).in_(lower_names),
            ),
        )
    )
    user_ids = list(result.scalars().all())

    # If none matched, fall back to admins so someone gets notified
    if not user_ids:
        return await get_admin_user_ids(db)

    return user_ids


class WaitWebhookHandler(StepActionHandler):
    """Pauses the pipeline until an external webhook hits the gate endpoint.

    The companion API ``POST /api/v1/gates/webhook/{step_id}`` writes a JSON
    payload to ``gate:webhook:{step_id}`` in Redis. This handler polls that
    key until it appears or the timeout elapses.
    """

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        timeout = config.get("timeout", _DEFAULT_WEBHOOK_TIMEOUT)
        name = config.get("name", "webhook")
        match_rules: dict[str, str] = config.get("match", {})

        settings = get_settings()
        redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
        key = _gate_key(str(ctx.step_id), "webhook")

        gate_token = secrets.token_urlsafe(32)
        await redis_client.set(
            _gate_token_key(str(ctx.step_id)), gate_token, ex=timeout + 300,
        )

        yield LogLine(
            stream="system",
            content=f"Waiting for webhook '{name}' (timeout {timeout}s)…\n",
        )
        yield LogLine(
            stream="system",
            content=f"Gate endpoint: POST /api/v1/gates/webhook/{ctx.step_id}\n",
        )
        yield LogLine(
            stream="system",
            content=f"Gate token (pass via X-Gate-Token header): {gate_token}\n",
        )

        try:
            elapsed = 0.0
            last_log = 0.0
            while elapsed < timeout:
                if await build_cancel_requested(redis_client, ctx.build_id):
                    yield LogLine(stream="system", content="Build cancelled.\n")
                    yield StepResult(exit_code=1, status="cancelled")
                    return
                raw = await redis_client.get(key)
                if raw is not None:
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        payload = {}

                    if match_rules:
                        mismatches = [
                            k for k, v in match_rules.items()
                            if str(payload.get(k, "")) != str(v)
                        ]
                        if mismatches:
                            yield LogLine(
                                stream="system",
                                content=f"Webhook received but match failed on: {mismatches}. Continuing to wait…\n",
                            )
                            await redis_client.delete(key)
                            await asyncio.sleep(_POLL_INTERVAL)
                            elapsed += _POLL_INTERVAL
                            continue

                    yield LogLine(stream="system", content=f"Webhook '{name}' received.\n")
                    await redis_client.delete(key)
                    yield StepResult(
                        exit_code=0,
                        status="success",
                        outputs={"webhook_payload": payload},
                    )
                    return

                if elapsed - last_log >= 30:
                    remaining = int(timeout - elapsed)
                    yield LogLine(
                        stream="system",
                        content=f"Still waiting for webhook '{name}'… ({remaining}s remaining)\n",
                    )
                    last_log = elapsed

                await asyncio.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL

            yield LogLine(stream="system", content=f"Webhook '{name}' timed out after {timeout}s.\n")
            yield StepResult(exit_code=1, status="failed", error="Webhook timeout")
        finally:
            await redis_client.aclose()

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        timeout = config.get("timeout", _DEFAULT_WEBHOOK_TIMEOUT)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append("wait_webhook 'timeout' must be a positive number")
        return errors


class WaitInputHandler(StepActionHandler):
    """Pauses the pipeline until a user approves or rejects via the UI.

    The companion API ``POST /api/v1/gates/input/{step_id}`` writes
    ``{"approved": true/false, "user": "..."}`` to ``gate:input:{step_id}``
    in Redis.
    """

    async def execute(
        self,
        config: dict[str, Any],
        ctx: StepContext,
        db: AsyncSession,
    ) -> AsyncIterator[LogLine | StepResult]:
        prompt = config.get("prompt", "Manual approval required")
        timeout = config.get("timeout", _DEFAULT_INPUT_TIMEOUT)
        allowed_users: list[str] = config.get("allowed_users", [])

        yield LogLine(stream="system", content=f"⏸ {prompt}\n")
        yield LogLine(
            stream="system",
            content=f"Approval endpoint: POST /api/v1/gates/input/{ctx.step_id}\n",
        )
        if allowed_users:
            yield LogLine(
                stream="system",
                content=f"Allowed approvers: {', '.join(allowed_users)}\n",
            )

        settings = get_settings()
        redis_client = aioredis.from_url(settings.MEGOOCI_REDIS_URL, decode_responses=True)
        key = _gate_key(str(ctx.step_id), "input")

        # Send in-app notifications to approvers
        try:
            approver_ids = await _resolve_approver_user_ids(db, allowed_users)
            if approver_ids:
                await notify_users(
                    db,
                    redis_client,
                    user_ids=approver_ids,
                    type="approval_required",
                    title=f"Approval required: {ctx.step_name}",
                    body=prompt,
                    entity_type="build",
                    entity_id=ctx.build_id,
                )
                await db.commit()
        except Exception:
            # Don't fail the gate if notification delivery fails
            await db.rollback()

        try:
            elapsed = 0.0
            last_log = 0.0
            while elapsed < timeout:
                if await build_cancel_requested(redis_client, ctx.build_id):
                    yield LogLine(stream="system", content="Build cancelled.\n")
                    yield StepResult(exit_code=1, status="cancelled")
                    return
                raw = await redis_client.get(key)
                if raw is not None:
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        payload = {}

                    user = payload.get("user", "unknown")
                    email = payload.get("email", "")
                    approved = payload.get("approved", False)

                    if allowed_users:
                        allowed_lower = [u.lower() for u in allowed_users]
                        user_matches = (
                            user.lower() in allowed_lower
                            or email.lower() in allowed_lower
                        )
                        if not user_matches:
                            yield LogLine(
                                stream="system",
                                content=f"User '{user}' ({email}) is not in the allowed approvers list. Ignoring.\n",
                            )
                            await redis_client.delete(key)
                            await asyncio.sleep(_POLL_INTERVAL)
                            elapsed += _POLL_INTERVAL
                            continue

                    await redis_client.delete(key)

                    if approved:
                        yield LogLine(stream="system", content=f"Approved by {user}.\n")
                        yield StepResult(exit_code=0, status="success", outputs={"approved_by": user})
                    else:
                        yield LogLine(stream="system", content=f"Rejected by {user}.\n")
                        yield StepResult(exit_code=1, status="failed", error=f"Rejected by {user}")
                    return

                if elapsed - last_log >= 60:
                    remaining = int(timeout - elapsed)
                    yield LogLine(
                        stream="system",
                        content=f"Waiting for approval… ({remaining}s remaining)\n",
                    )
                    last_log = elapsed

                await asyncio.sleep(_POLL_INTERVAL)
                elapsed += _POLL_INTERVAL

            yield LogLine(stream="system", content=f"Approval timed out after {timeout}s.\n")
            yield StepResult(exit_code=1, status="failed", error="Approval timeout")
        finally:
            await redis_client.aclose()

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        timeout = config.get("timeout", _DEFAULT_INPUT_TIMEOUT)
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            errors.append("wait_input 'timeout' must be a positive number")
        return errors


register("wait_webhook", WaitWebhookHandler())
register("wait_input", WaitInputHandler())
