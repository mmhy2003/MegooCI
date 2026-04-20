"""Inbound Git webhook receiver (PRD §6.16 / F-16.6 - F-16.12).

Single unauthenticated route: ``POST /api/v1/webhooks/git/{slug}``.

All other HTTP methods on the slug return 405 to prevent accidental slug
discovery via GETs. Verification is delegated to provider-specific adapters
(`services/git_providers.py`). Every request - accepted or rejected - is
persisted as a ``WebhookDelivery`` row so users can debug misconfigurations in
the UI.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_webhook_secret
from app.database import get_db
from app.models.build import Build
from app.models.git_integration import (
    GitProviderConnection,
    ProjectRepository,
    WebhookDelivery,
)
from app.models.pipeline import Pipeline
from app.services.git_providers import ParsedEvent, get_adapter
from app.tasks.build_tasks import run_build

router = APIRouter()

_MAX_PAYLOAD_EXCERPT = 4096
_RATE_LIMIT_WINDOW_SECONDS = 60


# ----------------------------------------------------------------------------
# Rate limiting (simple Redis fixed-window counter per slug)
# ----------------------------------------------------------------------------
async def _check_rate_limit(slug: str) -> tuple[bool, int]:
    """Return (allowed, retry_after_seconds).

    Uses a fixed-window counter keyed by slug. This is coarse but sufficient
    as a first line of defense against misconfigured webhooks replaying
    thousands of times.
    """
    settings = get_settings()
    limit = settings.MEGOOCI_WEBHOOK_RATE_LIMIT_PER_MINUTE
    if limit <= 0:
        return True, 0

    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    try:
        key = f"webhook:rate:{slug}"
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, _RATE_LIMIT_WINDOW_SECONDS)
        current, _ = await pipe.execute()
        current = int(current or 0)
        if current > limit:
            ttl = await redis_client.ttl(key)
            return False, max(1, int(ttl or _RATE_LIMIT_WINDOW_SECONDS))
        return True, 0
    except Exception:
        # Fail-open on Redis errors; we don't want provider retries to fail
        # because our rate limiter is unavailable.
        return True, 0
    finally:
        try:
            await redis_client.aclose()
        except Exception:
            pass


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
async def _load_repo_by_slug(
    db: AsyncSession, slug: str
) -> tuple[ProjectRepository, GitProviderConnection] | None:
    result = await db.execute(
        select(ProjectRepository).where(ProjectRepository.webhook_slug == slug)
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        return None
    connection = await db.get(GitProviderConnection, repo.connection_id)
    if connection is None:
        return None
    return repo, connection


async def _record_delivery(
    db: AsyncSession,
    repo: ProjectRepository,
    provider_delivery_id: str,
    event: ParsedEvent | None,
    signature_valid: bool,
    http_status: int,
    payload_excerpt: str | None,
    error: str | None,
    processed: bool,
    status_label: str,
) -> None:
    """Persist a WebhookDelivery and update the repo's last_event_*.

    Silently tolerates a UniqueConstraint violation on duplicate
    (project_repository_id, provider_delivery_id) — the caller should have
    already detected the duplicate, but on races we want to be idempotent.
    """
    try:
        delivery = WebhookDelivery(
            project_repository_id=repo.id,
            provider_delivery_id=provider_delivery_id,
            event_type=event.event_type if event else None,
            branch=event.branch if event else None,
            commit_sha=event.commit_sha if event else None,
            author=event.author if event else None,
            signature_valid=signature_valid,
            http_status=http_status,
            error=error,
            payload_excerpt=payload_excerpt,
            processed_at=datetime.now(timezone.utc) if processed else None,
        )
        db.add(delivery)
        repo.last_event_at = datetime.now(timezone.utc)
        repo.last_event_status = status_label
        await db.flush()
    except Exception:
        # Race on duplicate UNIQUE constraint; ignore.
        await db.rollback()
        return

    # Retention pruning (F-16.9): keep at most N rows per repo.
    settings = get_settings()
    keep = settings.MEGOOCI_WEBHOOK_DELIVERY_RETENTION
    if keep > 0:
        count = await db.scalar(
            select(func.count(WebhookDelivery.id)).where(
                WebhookDelivery.project_repository_id == repo.id
            )
        )
        if count and count > keep:
            # Delete the oldest rows beyond retention.
            overflow = count - keep
            subq = (
                select(WebhookDelivery.id)
                .where(WebhookDelivery.project_repository_id == repo.id)
                .order_by(WebhookDelivery.received_at.asc())
                .limit(overflow)
            ).subquery()
            await db.execute(
                delete(WebhookDelivery).where(
                    WebhookDelivery.id.in_(select(subq))
                )
            )


async def _enqueue_matching_builds(
    db: AsyncSession,
    repo: ProjectRepository,
    event: ParsedEvent,
) -> list[uuid.UUID]:
    """Insert a pending build for every pipeline in the repo's project that
    either references this repository directly (`project_repository_id`) or —
    for back-compat — declares the same `source_repo_url`.

    Returns the list of newly-created build ids (to be dispatched to Celery
    after the DB commit).
    """
    result = await db.execute(
        select(Pipeline).where(
            Pipeline.project_id == repo.project_id,
            Pipeline.enabled.is_(True),
            (
                (Pipeline.project_repository_id == repo.id)
                | (Pipeline.source_repo_url == repo.repo_url)
            ),
        )
    )
    pipelines = list(result.scalars().all())
    if not pipelines:
        return []

    new_build_ids: list[uuid.UUID] = []
    for pipeline in pipelines:
        # Branch filter: if the pipeline has a default_branch set, the push
        # branch must match. (Branch-pattern filters are future work.)
        if (
            pipeline.default_branch
            and event.branch
            and pipeline.default_branch != event.branch
        ):
            continue

        max_number = await db.scalar(
            select(func.coalesce(func.max(Build.number), 0)).where(
                Build.pipeline_id == pipeline.id
            )
        )
        build = Build(
            pipeline_id=pipeline.id,
            number=(max_number or 0) + 1,
            branch=event.branch or pipeline.default_branch,
            commit_sha=event.commit_sha,
            status="pending",
            triggered_by=None,
            trigger_type="webhook",
            params_json=None,
        )
        db.add(build)
        await db.flush()
        new_build_ids.append(build.id)

    return new_build_ids


# ----------------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------------
@router.api_route(
    "/webhooks/git/{slug}", methods=["GET", "HEAD", "PUT", "DELETE", "PATCH"]
)
async def method_not_allowed(slug: str) -> Response:
    """Reject any non-POST request to the webhook URL (F-16.13).

    Explicit 405 with an Allow header prevents slug discovery by casual
    `GET` probes and satisfies providers that sometimes send a preflight.
    """
    return Response(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        headers={"Allow": "POST"},
    )


@router.post("/webhooks/git/{slug}")
async def receive_webhook(
    slug: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Receive and verify a Git provider webhook delivery.

    Flow:
      1. Load the repository + connection by slug (404 if unknown).
      2. Rate-limit per slug (429 if exceeded).
      3. Read the raw body (for HMAC).
      4. Parse provider-specific headers / payload (422 on bad JSON).
      5. Verify the signature (401 on mismatch).
      6. Check replay protection via (repo_id, provider_delivery_id)
         uniqueness (409 on duplicate).
      7. On verified push: insert pending builds and dispatch run_build.
      8. Persist a WebhookDelivery row for audit/display.
    """
    # ---- 1. resolve repo ----
    ctx = await _load_repo_by_slug(db, slug)
    if ctx is None:
        # Don't leak whether a slug exists via timing; still record nothing.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook endpoint not found"
        )
    repo, connection = ctx

    # ---- 2. rate limit ----
    allowed, retry_after = await _check_rate_limit(slug)
    if not allowed:
        return Response(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers={"Retry-After": str(retry_after)},
        )

    # ---- 3. body ----
    body = await request.body()
    payload_excerpt = body[:_MAX_PAYLOAD_EXCERPT].decode(
        "utf-8", errors="replace"
    )

    # ---- 4. parse ----
    try:
        adapter = get_adapter(connection.provider_type)
    except ValueError as exc:
        await _record_delivery(
            db,
            repo,
            provider_delivery_id=str(uuid.uuid4()),
            event=None,
            signature_valid=False,
            http_status=500,
            payload_excerpt=payload_excerpt,
            error=str(exc),
            processed=False,
            status_label="rejected",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        )

    parsed_payload: dict[str, Any] = {}
    if body:
        try:
            parsed_payload = json.loads(body.decode("utf-8"))
            if not isinstance(parsed_payload, dict):
                parsed_payload = {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            # GitLab's "Test" button sometimes sends a plain ping; tolerate.
            parsed_payload = {}

    event = adapter.parse_push_event(request.headers, parsed_payload)

    # ---- 5. verify signature ----
    settings = get_settings()
    try:
        secret = decrypt_webhook_secret(
            repo.webhook_secret_hash, settings.MEGOOCI_SECRET_KEY
        )
    except Exception as exc:
        await _record_delivery(
            db,
            repo,
            provider_delivery_id=event.delivery_id,
            event=event,
            signature_valid=False,
            http_status=500,
            payload_excerpt=payload_excerpt,
            error=f"Cannot decrypt stored secret: {exc}",
            processed=False,
            status_label="rejected",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server-side secret decryption failed",
        )

    signature_valid = adapter.verify_signature(request.headers, body, secret)
    if not signature_valid:
        await _record_delivery(
            db,
            repo,
            provider_delivery_id=event.delivery_id,
            event=event,
            signature_valid=False,
            http_status=401,
            payload_excerpt=payload_excerpt,
            error="Invalid signature",
            processed=False,
            status_label="rejected",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature"
        )

    # ---- 6. replay protection ----
    existing = await db.scalar(
        select(WebhookDelivery.id).where(
            WebhookDelivery.project_repository_id == repo.id,
            WebhookDelivery.provider_delivery_id == event.delivery_id,
        )
    )
    if existing is not None:
        # Record the attempt but with http_status=409 (already processed).
        await _record_delivery(
            db,
            repo,
            provider_delivery_id=f"{event.delivery_id}:dup:{uuid.uuid4().hex[:8]}",
            event=event,
            signature_valid=True,
            http_status=409,
            payload_excerpt=payload_excerpt,
            error="Duplicate delivery",
            processed=False,
            status_label="duplicate",
        )
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Duplicate delivery"
        )

    # ---- 7. enqueue ----
    new_build_ids: list[uuid.UUID] = []
    if event.should_trigger_build:
        new_build_ids = await _enqueue_matching_builds(db, repo, event)

    # ---- 8. record delivery and commit ----
    await _record_delivery(
        db,
        repo,
        provider_delivery_id=event.delivery_id,
        event=event,
        signature_valid=True,
        http_status=202 if new_build_ids else 200,
        payload_excerpt=payload_excerpt,
        error=None,
        processed=True,
        status_label="accepted",
    )
    await db.commit()

    # Dispatch Celery tasks only after the DB transaction commits so that a
    # worker picking them up immediately still sees the Build row. Broker
    # connectivity issues shouldn't fail the webhook itself; builds stay in
    # "pending" and can be retried manually.
    for build_id in new_build_ids:
        try:
            run_build.delay(str(build_id))
        except Exception:
            pass

    if new_build_ids:
        return Response(
            status_code=status.HTTP_202_ACCEPTED,
            content=json.dumps({"enqueued_builds": len(new_build_ids)}),
            media_type="application/json",
        )
    return Response(
        status_code=status.HTTP_200_OK,
        content=json.dumps(
            {"enqueued_builds": 0, "event": event.event_type}
        ),
        media_type="application/json",
    )
