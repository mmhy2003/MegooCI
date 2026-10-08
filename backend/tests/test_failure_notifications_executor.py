"""The executor notifies about a failed build after the agent has been
released, and without ever disturbing the build's result."""
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._failure_notifications import FakeRedis, seed_build, seed_channel
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def slack(monkeypatch):
    sent = []

    async def fake_slack(config, message, recipient):
        sent.append(message)

    monkeypatch.setattr("app.services.notification_service._send_slack", fake_slack)
    return sent


@pytest.fixture
def fake_redis(monkeypatch):
    """The notifier opens its own Redis client for the build-log line."""
    from app.services import build_executor

    redis = FakeRedis()
    monkeypatch.setattr(build_executor.aioredis, "from_url", lambda *a, **k: redis)
    return redis


async def _run_stages(sf, monkeypatch, build_id, step_result, *, on_step=None):
    """Run the real executor loop with the step itself faked. Returns what
    _run_build_stages returns: a snapshot of the build as it ended, or None."""
    from app.services import build_executor
    from app.services.step_actions.base import StepResult

    async def scope(db, build):
        return {"TOKEN": "s3"}, {"REGION": "eu"}, {
            "build": {"number": str(build.number), "branch": build.branch or "",
                      "commit": build.commit_sha or ""},
            "pipeline": {"name": "deploy-staging"}, "project": {"name": "Inbox Staging"},
        }

    async def fake_step(*, step, build, **kwargs):
        if on_step is not None:
            await on_step(build)
        return StepResult(**step_result)

    monkeypatch.setattr(build_executor, "_load_scope_context", scope)
    monkeypatch.setattr(build_executor, "_execute_step", fake_step)
    return await build_executor._run_build_stages(
        build_id=build_id, claimed_agent_id=uuid.uuid4(),
        session_factory=sf, redis_client=FakeRedis(), channel="build:x:logs",
    )


async def _seed(sf, **kwargs):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, build_status="pending", step_status="pending", **kwargs)
        await db.commit()
    return ids


async def _build_status(sf, build_id):
    from app.models.build import Build

    async with sf() as db:
        return (await db.get(Build, build_id)).status


FAILED = {"exit_code": 1, "status": "failed"}
OK = {"exit_code": 0, "status": "success"}


# ── what the stage loop hands back ──────────────────────────────────────

async def test_failed_build_returns_a_snapshot_of_what_failed(sf, monkeypatch):
    ids = await _seed(sf)

    failed = await _run_stages(sf, monkeypatch, ids["build"], FAILED)

    assert await _build_status(sf, ids["build"]) == "failed"
    assert failed is not None
    assert failed.build_id == ids["build"] and failed.pipeline_id == ids["pipeline"]
    assert (failed.number, failed.failed_stage, failed.failed_step) == (428, "deploy", "apply manifests")
    assert failed.failed_step_id == ids["step"]
    assert failed.secrets == {"TOKEN": "s3"} and failed.env_vars == {"REGION": "eu"}
    assert failed.builtins["pipeline"]["name"] == "deploy-staging"


async def test_agent_lost_mid_build_is_a_failed_build(sf, monkeypatch):
    """When the build's agent goes away, its step fails and so does the build."""
    ids = await _seed(sf)

    failed = await _run_stages(sf, monkeypatch, ids["build"], {
        **FAILED, "error": "No build agent is available to execute this step.",
    })

    assert failed is not None and failed.failed_step == "apply manifests"


async def test_successful_build_returns_a_snapshot_without_a_failure(sf, monkeypatch):
    ids = await _seed(sf)

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], OK)

    assert await _build_status(sf, ids["build"]) == "success"
    assert snapshot.status == "success"
    assert (snapshot.failed_stage, snapshot.failed_step, snapshot.failed_step_id) == ("", "", None)


async def test_cancelled_build_returns_a_cancelled_snapshot(sf, monkeypatch):
    from app.models.build import Build

    ids = await _seed(sf)

    async def cancel(build):
        async with sf() as other:
            (await other.get(Build, build.id)).status = "cancelled"
            await other.commit()

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], OK, on_step=cancel)

    assert await _build_status(sf, ids["build"]) == "cancelled"
    assert snapshot.status == "cancelled" and snapshot.failed_step == ""


async def test_snapshot_survives_a_rollback_in_the_in_app_notice(sf, monkeypatch):
    """The in-app notice rolls the session back when it fails, which expires
    every loaded object. The snapshot must have been taken before that."""
    from app.models.build import Build
    from app.models.user import User
    from app.services import build_executor

    ids = await _seed(sf)
    async with sf() as db:
        user = User(id=uuid.uuid4(), email=f"{uuid.uuid4().hex}@e.com", name="dev")
        db.add(user)
        await db.flush()
        (await db.get(Build, ids["build"])).triggered_by = user.id
        await db.commit()

    async def broken_notify_user(db, *args, **kwargs):
        await db.execute(select(Build))  # touch the session, then fail
        raise RuntimeError("in-app notifications are down")

    monkeypatch.setattr(build_executor, "notify_user", broken_notify_user)

    failed = await _run_stages(sf, monkeypatch, ids["build"], FAILED)

    assert failed is not None
    assert (failed.failed_stage, failed.failed_step) == ("deploy", "apply manifests")


# ── sending ─────────────────────────────────────────────────────────────

async def test_notifier_sends_and_records_a_delivery(sf, slack, fake_redis, monkeypatch):
    from app.models.notification import NotificationDelivery
    from app.services.build_executor import _send_notifications

    ids = await _seed(sf)
    failed = await _run_stages(sf, monkeypatch, ids["build"], FAILED)

    await _send_notifications(failed, sf)

    assert len(slack) == 1
    assert "Build #428 of deploy-staging failed" in slack[0]
    assert "Failed at: stage \"deploy\", step \"apply manifests\"" in slack[0]
    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.build_id == ids["build"] and row.status == "sent"


async def test_unknown_channel_is_reported_in_the_build_log(sf, slack, fake_redis, monkeypatch):
    from app.models.build import LogChunk
    from app.services.build_executor import _send_notifications

    yaml_content = (
        "name: deploy-staging\n"
        "notifications:\n  on_failure:\n    - no-such-channel\n    - deploy-alerts\n"
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )
    ids = await _seed(sf, yaml_content=yaml_content)
    failed = await _run_stages(sf, monkeypatch, ids["build"], FAILED)

    await _send_notifications(failed, sf)

    async with sf() as db:
        lines = [c.content for c in (await db.execute(
            select(LogChunk).where(LogChunk.step_id == ids["step"], LogChunk.stream == "system")
        )).scalars()]
    assert any("channel 'no-such-channel' was not found" in line for line in lines), lines
    assert len(slack) == 1, "the entry after the bad one must still be sent"
    assert fake_redis.published, "the log line should also be published live"


async def test_notifier_never_raises(sf, fake_redis, monkeypatch):
    from app.services.build_executor import _send_notifications

    ids = await _seed(sf)
    failed = await _run_stages(sf, monkeypatch, ids["build"], FAILED)

    async def explode(*args, **kwargs):
        raise RuntimeError("notification subsystem is down")

    monkeypatch.setattr("app.services.build_executor.send_build_notifications", explode)

    await _send_notifications(failed, sf)  # must not raise

    assert await _build_status(sf, ids["build"]) == "failed"


# ── ordering in execute_build ───────────────────────────────────────────

async def test_agent_is_released_and_next_build_dispatched_before_notifying(sf, fake_redis, monkeypatch):
    """Sending can be slow. It must not keep the agent reserved or delay the
    next queued build."""
    from app.models.agent import Agent
    from app.services import build_executor
    from app.services.build_notifications import BuildSnapshot

    ids = await _seed(sf)
    agent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with sf() as db:
        db.add(Agent(id=agent_id, name="a1", status="online", enabled=True,
                     connected_at=now, last_seen_at=now, current_build_id=ids["build"]))
        await db.commit()

    order = []
    snapshot = BuildSnapshot(
        build_id=ids["build"], pipeline_id=ids["pipeline"], number=428, failed_stage="deploy",
        failed_step="apply manifests", failed_step_id=ids["step"], secrets={}, env_vars={},
        builtins={},
    )

    async def fake_stages(**kwargs):
        return snapshot

    async def fake_release(db, agent, build):
        order.append("release")

    async def fake_send_finished(agent, build):
        pass

    async def fake_dispatch(**kwargs):
        order.append("dispatch")

    async def fake_notify(failed, session_factory):
        order.append("notify")
        assert failed is snapshot

    monkeypatch.setattr(build_executor, "_run_build_stages", fake_stages)
    monkeypatch.setattr(build_executor, "release_agent", fake_release)
    monkeypatch.setattr(build_executor, "send_build_finished", fake_send_finished)
    monkeypatch.setattr(build_executor, "dispatch_pending_builds", fake_dispatch)
    monkeypatch.setattr(build_executor, "_send_notifications", fake_notify)

    await build_executor.execute_build(ids["build"], session_factory=sf, claimed_agent_id=agent_id)

    assert order == ["release", "dispatch", "notify"]


async def test_no_notification_when_the_stage_loop_returns_nothing(sf, fake_redis, monkeypatch):
    from app.models.agent import Agent
    from app.services import build_executor

    ids = await _seed(sf)
    agent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with sf() as db:
        db.add(Agent(id=agent_id, name="a1", status="online", enabled=True,
                     connected_at=now, last_seen_at=now, current_build_id=ids["build"]))
        await db.commit()

    calls = []

    async def fake_stages(**kwargs):
        return None

    async def noop(*args, **kwargs):
        pass

    async def fake_notify(failed, session_factory):
        calls.append(failed)

    monkeypatch.setattr(build_executor, "_run_build_stages", fake_stages)
    monkeypatch.setattr(build_executor, "release_agent", noop)
    monkeypatch.setattr(build_executor, "send_build_finished", noop)
    monkeypatch.setattr(build_executor, "dispatch_pending_builds", noop)
    monkeypatch.setattr(build_executor, "_send_notifications", fake_notify)

    await build_executor.execute_build(ids["build"], session_factory=sf, claimed_agent_id=agent_id)

    assert calls == []
