"""Controller-side dispatcher that hands build steps to remote agents.

Design (PRD §6.3 / F-3.4 / F-3.6):

- Each registered agent owns a Redis **list** at ``agent:{id}:tasks``. The
  WebSocket handler for that agent BLPOPs from it and forwards messages
  over the socket.
- Step completion is signalled back to the dispatcher through a Redis
  **pub/sub** channel at ``step:{step_id}:result``. The payload is a small
  JSON document: ``{"exit_code": int, "status": "success"|"failed"}``.
- Log lines from the agent are ingested by the WebSocket handler directly
  (see ``api/v1/agents_ws.py``) — they go into the same
  ``build:{build_id}:logs`` pub/sub channel the local executor already uses
  and are persisted as ``LogChunk`` rows, so the existing UI works
  unchanged.

The dispatcher is intentionally simple in Phase 1:

- **Agent selection**: any agent whose ``status == 'online'`` and whose
  ``connected_at is not None`` is a candidate. If multiple match, we pick
  the one with the oldest ``last_seen_at`` (a cheap round-robin proxy).
  Label-based selection is deferred to a follow-up.
- **No reservation protocol**: we optimistically dispatch to the chosen
  agent and trust its local semaphore to respect ``capacity``. If the
  agent disconnects mid-step, the controller times out waiting on the
  completion channel and the build executor falls back to local.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.models.agent import Agent
from app.models.build import Build, Step

logger = logging.getLogger(__name__)


# How long we wait for a dispatch to produce a result before giving up. This
# bound must be high enough for real build steps (think `docker build`,
# `npm install`) but low enough that a half-dead agent doesn't hold a build
# forever. The caller (build executor) is expected to fall back to local
# execution on timeout.
_DEFAULT_STEP_TIMEOUT_SECONDS = 60 * 60  # 1 hour


def tasks_list_key(agent_id: uuid.UUID | str) -> str:
    """Redis LIST that an agent's WS handler BLPOPs task frames from."""
    return f"agent:{agent_id}:tasks"


def agent_control_channel(agent_id: uuid.UUID | str) -> str:
    """Per-agent Redis pub/sub channel for out-of-band control messages
    (today: cancel). Subscribed once for the lifetime of the WS session."""
    return f"agent:{agent_id}:control"


def step_result_channel(step_id: uuid.UUID | str) -> str:
    """Per-step pub/sub channel the agent writes to on completion."""
    return f"step:{step_id}:result"


def agent_matches_requirements(
    agent: Agent, requirements: dict[str, Any] | None
) -> bool:
    """True if *agent* satisfies the given runs_on requirements.

    Requirements is a dict that may contain any subset of:
      - ``os``    : exact match (case-insensitive)
      - ``arch``  : exact match (case-insensitive)
      - ``labels``: list — agent.labels must be a superset

    None / empty requirements always match.
    """
    if not requirements:
        return True

    req_os = (requirements.get("os") or "").strip().lower() if isinstance(requirements.get("os"), str) else ""
    if req_os and (agent.os or "").strip().lower() != req_os:
        return False

    req_arch = (requirements.get("arch") or "").strip().lower() if isinstance(requirements.get("arch"), str) else ""
    if req_arch and (agent.arch or "").strip().lower() != req_arch:
        return False

    req_labels = requirements.get("labels") or []
    if req_labels:
        agent_labels = {str(l).strip().lower() for l in (agent.labels or [])}
        needed = {str(l).strip().lower() for l in req_labels}
        if not needed.issubset(agent_labels):
            return False

    return True


async def pick_online_agent(
    db: AsyncSession,
    requirements: dict[str, Any] | None = None,
) -> Agent | None:
    """Return an idle, currently-online agent that matches *requirements*.

    An agent is considered "available" when:
    - ``enabled`` is True (operators can disable agents for maintenance),
    - ``status == 'online'`` and ``connected_at is not None`` (WS up),
    - ``current_build_id IS NULL`` (not already executing a build).

    When *requirements* is provided (e.g. ``{"os": "linux"}``), the agent
    must also match those constraints — see ``agent_matches_requirements``.
    Picks the least-recently-used matching agent as a poor-man's load
    balancer.
    """
    # Eligibility filters that translate cleanly to SQL.
    query = (
        select(Agent)
        .where(
            Agent.enabled.is_(True),
            Agent.status == "online",
            Agent.connected_at.isnot(None),
            Agent.current_build_id.is_(None),
        )
        .order_by(Agent.last_seen_at.asc())
    )

    # os / arch are cheap to push down — labels live in JSON, so we filter
    # those in Python after the query. With a typical handful of agents
    # this is fine; if the agent pool grows large we can switch to a
    # JSONB containment query.
    if requirements:
        req_os = requirements.get("os")
        if isinstance(req_os, str) and req_os.strip():
            query = query.where(func.lower(Agent.os) == req_os.strip().lower())
        req_arch = requirements.get("arch")
        if isinstance(req_arch, str) and req_arch.strip():
            query = query.where(func.lower(Agent.arch) == req_arch.strip().lower())

    result = await db.execute(query)
    for agent in result.scalars():
        if agent_matches_requirements(agent, requirements):
            return agent
    return None


async def claim_agent(
    db: AsyncSession, agent_id: uuid.UUID, build_id: uuid.UUID
) -> bool:
    """Atomically mark an agent as busy with a specific build.

    Returns True if the agent was successfully claimed (was idle), False if
    it was already busy (another build beat us to it). Uses a database-level
    conditional UPDATE to prevent races between concurrent Celery workers.
    """
    result = await db.execute(
        update(Agent)
        .where(
            Agent.id == agent_id,
            Agent.current_build_id.is_(None),
        )
        .values(current_build_id=build_id)
    )
    await db.commit()
    return result.rowcount > 0  # type: ignore[union-attr]


async def release_agent(
    db: AsyncSession, agent_id: uuid.UUID, build_id: uuid.UUID | None = None
) -> None:
    """Mark an agent as idle by clearing current_build_id.

    If *build_id* is provided, only clear if it matches (prevents a stale
    release from clobbering a newer claim).
    """
    stmt = update(Agent).where(Agent.id == agent_id)
    if build_id is not None:
        stmt = stmt.where(Agent.current_build_id == build_id)
    stmt = stmt.values(current_build_id=None)
    await db.execute(stmt)
    await db.commit()


# Build statuses that mean "this build will never run on the reserved agent
# again". A reservation pointing at one of these (or at a build that no longer
# exists) is stale and safe to clear.
_TERMINAL_BUILD_STATES = ("success", "failed", "cancelled")


async def reconcile_stale_reservations(db: AsyncSession) -> int:
    """Clear leaked ``current_build_id`` reservations and return how many.

    An agent's reservation can leak when the Celery worker that claimed it dies
    (OOM / redeploy / SIGKILL) before ``release_agent`` runs in
    ``execute_build``'s ``finally``, or when a build is cancelled / deleted
    while an agent is still reserved for it. Because the agent's WebSocket lives
    in the API process — not the worker — the agent stays online and idle, yet
    ``pick_online_agent`` skips it forever (``current_build_id IS NOT NULL``).
    Over time this silently shrinks capacity and pending builds pile up despite
    "free" agents.

    This is the level-triggered safety net: it clears the reservation for any
    agent whose reserved build is in a terminal state or no longer exists.
    Builds that are still ``pending`` or ``running`` are left untouched so we
    never yank an agent off live (or about-to-start) work. The conditional
    UPDATE re-checks ``current_build_id`` so a concurrent re-claim is never
    clobbered.
    """
    result = await db.execute(
        select(Agent.id, Agent.current_build_id).where(
            Agent.current_build_id.isnot(None)
        )
    )
    reserved = [(aid, bid) for aid, bid in result.all() if bid is not None]
    if not reserved:
        return 0

    build_ids = {bid for _, bid in reserved}
    rows = await db.execute(
        select(Build.id, Build.status).where(Build.id.in_(build_ids))
    )
    status_by_build = {bid: status for bid, status in rows.all()}

    cleared = 0
    for agent_id, build_id in reserved:
        status = status_by_build.get(build_id)
        if status is not None and status not in _TERMINAL_BUILD_STATES:
            continue  # build still pending/running — keep the reservation
        res = await db.execute(
            update(Agent)
            .where(Agent.id == agent_id, Agent.current_build_id == build_id)
            .values(current_build_id=None)
        )
        cleared += res.rowcount or 0  # type: ignore[union-attr]

    if cleared:
        await db.commit()
        logger.info("Reconciled %d stale agent reservation(s)", cleared)
    return cleared


async def reconcile_and_dispatch(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Periodic safety-net pass: heal leaked reservations, then run dispatch.

    Invoked on a timer by Celery Beat so a missed dispatch edge (a swallowed
    exception, a crashed worker, a lost wakeup) can never strand pending builds
    or freed agents for longer than the beat interval.
    """
    if session_factory is None:
        from app.database import async_session as session_factory

    async with session_factory() as db:
        try:
            await reconcile_stale_reservations(db)
        except Exception:
            logger.exception("reconcile_stale_reservations failed")

    await dispatch_pending_builds(session_factory=session_factory)


async def dispatch_pending_builds(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Check for pending builds and dispatch them if agents are available.

    Called after a build finishes (agent released) so queued pipelines are
    picked up automatically without polling.

    Iterates through the oldest pending builds whose runs_on requirements
    can be satisfied by an idle agent right now. A Windows build at the
    head of the queue should not block a Linux build behind it when only a
    Linux agent is free.

    Agents are **pre-claimed** before the Celery task is enqueued so that
    concurrent callers cannot race for the same agent. The claimed agent
    ID is forwarded to the worker so ``execute_build()`` can skip the
    pick-and-claim dance.

    *session_factory* lets a Celery worker pass its own loop-bound engine.
    The module-level ``async_session`` is bound to the event loop that was
    running at import time; a worker that spins up a fresh loop per task
    (see ``run_build``) must not reuse it or asyncpg raises "Future attached
    to a different loop" — which, swallowed by the caller, silently drops the
    dispatch. Defaults to the global factory for API-process callers.
    """
    from app.tasks.build_tasks import run_build

    if session_factory is None:
        from app.database import async_session as session_factory

    async with session_factory() as db:
        # Don't dispatch builds while maintenance mode is active.
        from app.api.v1.system import is_maintenance_mode
        if await is_maintenance_mode(db):
            return

        # Scan the queue head; bounded so this stays cheap even with a
        # large backlog.
        result = await db.execute(
            select(Build)
            .where(Build.status == "pending")
            .order_by(Build.created_at.asc())
            .limit(20)
        )
        pending_builds = list(result.scalars())
        if not pending_builds:
            return

        for pending in pending_builds:
            agent = await pick_online_agent(db, requirements=pending.runs_on)
            if agent is None:
                # No more free agents — stop trying further builds.
                continue

            # Pre-claim the agent *before* enqueuing so no other call
            # can race for it.
            claimed = await claim_agent(db, agent.id, pending.id)
            if not claimed:
                # Another caller grabbed this agent between our
                # SELECT and UPDATE — try the next pending build
                # (pick_online_agent will skip this agent now).
                continue

            logger.info(
                "Dispatching pending build %s to pre-claimed agent %s",
                pending.id, agent.name,
            )
            try:
                run_build.delay(str(pending.id), str(agent.id))
            except Exception:
                logger.exception("Failed to enqueue pending build %s", pending.id)
                # Release the agent we just claimed so it isn't stuck.
                try:
                    await release_agent(db, agent.id, pending.id)
                except Exception:
                    pass


async def dispatch_single_build(build_id: uuid.UUID) -> bool:
    """Attempt to dispatch a specific pending build to a free agent.

    Called from the manual "Dispatch" API endpoint. Returns True if the
    build was successfully enqueued, False if no agent was available or
    the build is not in ``pending`` status.
    """
    from app.database import async_session as _session_factory
    from app.tasks.build_tasks import run_build

    async with _session_factory() as db:
        # Don't dispatch builds while maintenance mode is active (mirrors
        # dispatch_pending_builds). The API endpoint also checks this to
        # return a clearer error, but guarding here protects any other caller.
        from app.api.v1.system import is_maintenance_mode
        if await is_maintenance_mode(db):
            return False

        result = await db.execute(
            select(Build).where(Build.id == build_id)
        )
        build = result.scalar_one_or_none()
        if build is None or build.status != "pending":
            return False

        agent = await pick_online_agent(db, requirements=build.runs_on)
        if agent is None:
            return False

        claimed = await claim_agent(db, agent.id, build_id)
        if not claimed:
            return False

        logger.info(
            "Manual dispatch: build %s → agent %s",
            build_id, agent.name,
        )
        try:
            run_build.delay(str(build_id), str(agent.id))
        except Exception:
            logger.exception("Failed to enqueue manually dispatched build %s", build_id)
            try:
                await release_agent(db, agent.id, build_id)
            except Exception:
                pass
            return False

        return True


async def dispatch_step_to_agent(
    step: Step,
    stage_name: str,
    build_id: uuid.UUID,
    agent_id: uuid.UUID,
    *,
    artifact_paths: list[str] | None = None,
    timeout_seconds: int = _DEFAULT_STEP_TIMEOUT_SECONDS,
    config_override: dict[str, Any] | None = None,
    command_override: str | None = None,
) -> dict[str, Any] | None:
    """Send a run_step message to the given agent and wait for its result.

    Returns the result dict (``{"exit_code": int, "status": "success"|"failed"}``)
    or ``None`` on timeout / infrastructure failure. Logs streamed back from
    the agent are handled separately by the WebSocket ingestion handler.

    When *config_override* or *command_override* are provided they replace
    the raw DB values so that server-side interpolation (secrets, env-vars)
    and enrichment (token resolution) are reflected in what the agent sees.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )

    payload: dict[str, Any] = {
        "type": "run_step",
        "assignment_id": str(uuid.uuid4()),
        "build_id": str(build_id),
        "stage_name": stage_name,
        "step_id": str(step.id),
        "step_name": step.name,
        "step_type": step.step_type or "run",
        "command": command_override if command_override is not None else (step.command or ""),
        "config": config_override if config_override is not None else (step.config_json or {}),
    }
    if artifact_paths:
        payload["artifact_paths"] = artifact_paths

    result_channel = step_result_channel(step.id)
    pubsub = redis_client.pubsub()
    await pubsub.subscribe(result_channel)

    try:
        # Enqueue the task for the agent's WS handler to pick up.
        await redis_client.rpush(tasks_list_key(agent_id), json.dumps(payload))

        # Wait for the agent to report completion.
        try:
            async with asyncio.timeout(timeout_seconds):
                async for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    try:
                        return json.loads(message["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
        except asyncio.TimeoutError:
            return None
        return None
    finally:
        await pubsub.unsubscribe(result_channel)
        await pubsub.aclose()
        await redis_client.aclose()


async def signal_cancel_step(agent_id: uuid.UUID, step_id: uuid.UUID) -> None:
    """Tell a specific agent to stop running the given step.

    Publishes on the agent's control channel; the agent's WS handler forwards
    the cancel frame over the socket.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    try:
        await redis_client.publish(
            agent_control_channel(agent_id),
            json.dumps({"type": "cancel_step", "step_id": str(step_id)}),
        )
    finally:
        await redis_client.aclose()


async def send_build_finished(agent_id: uuid.UUID, build_id: uuid.UUID) -> None:
    """Tell the agent that a build is fully complete so it can release the
    shared workspace directory.

    Pushes a ``build_finished`` frame onto the agent's task queue — the same
    queue the dispatcher loop reads from — so it is delivered over the WS
    connection in order after any pending step assignments.
    """
    settings = get_settings()
    redis_client = aioredis.from_url(
        settings.MEGOOCI_REDIS_URL, decode_responses=True
    )
    try:
        await redis_client.rpush(
            tasks_list_key(agent_id),
            json.dumps({
                "type": "build_finished",
                "build_id": str(build_id),
            }),
        )
    finally:
        await redis_client.aclose()
