"""The executor sends failure notifications when, and only when, a build ends failed."""
import os
import uuid

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


async def _run(sf, monkeypatch, build_id, step_result, *, on_step=None):
    """Run the real executor loop with the step itself faked."""
    from app.services import build_executor
    from app.services.step_actions.base import StepResult

    async def scope(db, build):
        return {}, {}, {"build": {"number": str(build.number), "branch": build.branch or "",
                                  "commit": build.commit_sha or ""},
                        "pipeline": {"name": "deploy-staging"}, "project": {"name": "Inbox Staging"}}

    async def fake_step(*, step, build, **kwargs):
        if on_step is not None:
            await on_step(build)
        return StepResult(**step_result)

    monkeypatch.setattr(build_executor, "_load_scope_context", scope)
    monkeypatch.setattr(build_executor, "_execute_step", fake_step)
    await build_executor._run_build_stages(
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


async def test_failed_build_sends_the_notification(sf, slack, monkeypatch):
    from app.models.notification import NotificationDelivery

    ids = await _seed(sf)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 1, "status": "failed"})

    assert await _build_status(sf, ids["build"]) == "failed"
    assert len(slack) == 1
    assert "Build #428 of deploy-staging failed" in slack[0]
    assert "Failed at: stage \"deploy\", step \"apply manifests\"" in slack[0]
    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.build_id == ids["build"] and row.status == "sent"


async def test_no_agent_failure_also_notifies(sf, slack, monkeypatch):
    """A step that fails because no agent was available is still a failed build."""
    ids = await _seed(sf)

    await _run(sf, monkeypatch, ids["build"], {
        "exit_code": 1, "status": "failed",
        "error": "No build agent is available to execute this step.",
    })

    assert len(slack) == 1


async def test_successful_build_sends_nothing(sf, slack, monkeypatch):
    ids = await _seed(sf)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 0, "status": "success"})

    assert await _build_status(sf, ids["build"]) == "success"
    assert slack == []


async def test_cancelled_build_sends_nothing(sf, slack, monkeypatch):
    from app.models.build import Build

    ids = await _seed(sf)

    async def cancel(build):
        async with sf() as other:
            (await other.get(Build, build.id)).status = "cancelled"
            await other.commit()

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 0, "status": "success"}, on_step=cancel)

    assert await _build_status(sf, ids["build"]) == "cancelled"
    assert slack == []


async def test_unknown_channel_is_reported_in_the_build_log(sf, slack, monkeypatch):
    from app.models.build import LogChunk

    yaml_content = (
        "name: deploy-staging\n"
        "notifications:\n  on_failure:\n    - no-such-channel\n"
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )
    ids = await _seed(sf, yaml_content=yaml_content)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 1, "status": "failed"})

    async with sf() as db:
        lines = [c.content for c in (await db.execute(
            select(LogChunk).where(LogChunk.step_id == ids["step"], LogChunk.stream == "system")
        )).scalars()]
    assert any("channel 'no-such-channel' was not found" in line for line in lines), lines
    assert await _build_status(sf, ids["build"]) == "failed"


async def test_error_while_sending_never_changes_the_build_result(sf, monkeypatch):
    ids = await _seed(sf)

    async def explode(*args, **kwargs):
        raise RuntimeError("notification subsystem is down")

    monkeypatch.setattr("app.services.build_notifications.send_failure_notifications", explode)

    await _run(sf, monkeypatch, ids["build"], {"exit_code": 1, "status": "failed"})

    assert await _build_status(sf, ids["build"]) == "failed"
