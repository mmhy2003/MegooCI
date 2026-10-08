"""The executor tells the pipeline's channels when a build starts, waits for
approval and ends — without delaying the build or changing its result."""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._failure_notifications import FakeRedis, seed_build, seed_channel
from tests._rbac import build_inmemory_factory

FAILED = {"exit_code": 1, "status": "failed"}
OK = {"exit_code": 0, "status": "success"}


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
    """The notifier opens its own Redis client for a build-log line."""
    from app.services import build_executor

    redis = FakeRedis()
    monkeypatch.setattr(build_executor.aioredis, "from_url", lambda *a, **k: redis)
    return redis


def _yaml(block: str) -> str:
    return (
        "name: deploy-staging\n"
        "notifications:\n" + block +
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )


async def _seed(sf, block, *, step_type="run", build_status="pending"):
    from app.models.build import Step

    async with sf() as db:
        await seed_channel(db, "team-chat")
        ids = await seed_build(db, yaml_content=_yaml(block), build_status=build_status,
                               step_status="pending")
        (await db.get(Step, ids["step"])).step_type = step_type
        await db.commit()
    return ids


async def _run_stages(sf, monkeypatch, build_id, step_result=OK, *, on_step=None):
    """Run the real executor loop with the step itself faked, then wait for
    the sends it started. Returns the snapshot of the build as it ended."""
    from app.services import build_executor
    from app.services.step_actions.base import StepResult

    async def scope(db, build):
        return {}, {}, {
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
    snapshot = await build_executor._run_build_stages(
        build_id=build_id, claimed_agent_id=uuid.uuid4(),
        session_factory=sf, redis_client=FakeRedis(), channel="build:x:logs",
    )
    await build_executor._wait_for_background_sends(build_id)
    return snapshot


def _first_lines(messages):
    return [message.splitlines()[0] for message in messages]


# ── on_start ────────────────────────────────────────────────────────────

async def test_on_start_is_sent_once_when_the_build_starts(sf, slack, monkeypatch):
    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    await _run_stages(sf, monkeypatch, ids["build"])

    assert _first_lines(slack) == ["Build #428 of deploy-staging started"]


async def test_the_first_step_does_not_wait_for_the_start_message(sf, monkeypatch):
    """A slow channel must not hold the build up."""
    ids = await _seed(sf, "  on_start:\n    - team-chat\n")
    release, order = asyncio.Event(), []

    async def slow_slack(config, message, recipient):
        order.append("send begins")
        await release.wait()
        order.append("send ends")

    async def step_runs(build):
        await asyncio.sleep(0.05)  # let the background send get going
        order.append("step runs")
        release.set()

    monkeypatch.setattr("app.services.notification_service._send_slack", slow_slack)

    await _run_stages(sf, monkeypatch, ids["build"], on_step=step_runs)

    assert order == ["send begins", "step runs", "send ends"]


async def test_nothing_is_started_for_a_pipeline_that_does_not_list_the_event(sf, slack, monkeypatch):
    from app.services import build_executor

    started = []
    monkeypatch.setattr(build_executor, "_send_in_background",
                        lambda snapshot, session_factory: started.append(snapshot))
    ids = await _seed(sf, "  on_failure:\n    - team-chat\n", step_type="wait_input")

    await _run_stages(sf, monkeypatch, ids["build"])

    assert started == []


async def test_the_start_snapshot_is_of_a_running_build(sf, monkeypatch):
    from app.services import build_executor

    started = []
    monkeypatch.setattr(build_executor, "_send_in_background",
                        lambda snapshot, session_factory: started.append(snapshot))
    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    ended = await _run_stages(sf, monkeypatch, ids["build"])

    assert [s.status for s in started] == ["running"]
    assert started[0].waiting_step == "" and started[0].log_step_id == ids["step"]
    assert ended.status == "success"


# ── on_waiting ──────────────────────────────────────────────────────────

async def test_on_waiting_is_sent_when_the_build_reaches_an_approval_step(sf, slack, monkeypatch):
    ids = await _seed(sf, "  on_waiting:\n    - team-chat\n", step_type="wait_input")

    await _run_stages(sf, monkeypatch, ids["build"])

    assert len(slack) == 1
    assert slack[0].splitlines()[0] == "Build #428 of deploy-staging is waiting for approval"
    assert "Waiting at: stage \"deploy\", step \"apply manifests\"" in slack[0]


@pytest.mark.parametrize("step_type", ["wait_webhook", "run", "notify"])
async def test_on_waiting_is_only_for_approval_steps(sf, slack, monkeypatch, step_type):
    ids = await _seed(sf, "  on_waiting:\n    - team-chat\n", step_type=step_type)

    await _run_stages(sf, monkeypatch, ids["build"])

    assert slack == []


async def test_the_approval_step_does_not_wait_for_the_message(sf, monkeypatch):
    ids = await _seed(sf, "  on_waiting:\n    - team-chat\n", step_type="wait_input")
    release, order = asyncio.Event(), []

    async def slow_slack(config, message, recipient):
        order.append("send begins")
        await release.wait()
        order.append("send ends")

    async def step_runs(build):
        await asyncio.sleep(0.05)
        order.append("step runs")
        release.set()

    monkeypatch.setattr("app.services.notification_service._send_slack", slow_slack)

    await _run_stages(sf, monkeypatch, ids["build"], on_step=step_runs)

    assert order == ["send begins", "step runs", "send ends"]


# ── the end of the build ────────────────────────────────────────────────

ALL_END = "".join(f"  {event}:\n    - channel: team-chat\n      message: \"{event}\"\n"
                  for event in ("on_success", "on_failure", "on_cancelled"))


@pytest.mark.parametrize("step_result, expected", [(OK, "on_success"), (FAILED, "on_failure")])
async def test_a_finished_build_sends_the_event_of_its_outcome(sf, slack, fake_redis, monkeypatch,
                                                               step_result, expected):
    from app.services import build_executor

    ids = await _seed(sf, ALL_END)
    snapshot = await _run_stages(sf, monkeypatch, ids["build"], step_result)

    await build_executor._send_notifications(snapshot, sf)

    assert slack == [expected]


async def test_a_build_cancelled_while_running_sends_on_cancelled(sf, slack, fake_redis, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor

    ids = await _seed(sf, ALL_END + "  on_complete:\n    - team-chat\n")

    async def cancel(build):
        async with sf() as other:
            (await other.get(Build, build.id)).status = "cancelled"
            await other.commit()

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], on_step=cancel)
    await build_executor._send_notifications(snapshot, sf)

    assert snapshot.status == "cancelled"
    assert slack == ["on_cancelled"], "and not on_complete as well: one message per channel"


async def test_start_and_end_of_one_build(sf, slack, fake_redis, monkeypatch):
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_complete:\n    - team-chat\n")

    snapshot = await _run_stages(sf, monkeypatch, ids["build"])
    await build_executor._send_notifications(snapshot, sf)

    assert _first_lines(slack) == [
        "Build #428 of deploy-staging started", "Build #428 of deploy-staging succeeded"]


async def test_the_block_is_read_when_the_event_happens_not_when_the_build_was_triggered(
        sf, slack, fake_redis, monkeypatch):
    """Someone edits the pipeline while a build of it is running."""
    from app.models.pipeline import Pipeline
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    async def edit_pipeline(build):
        async with sf() as other:
            pipeline = await other.get(Pipeline, ids["pipeline"])
            pipeline.yaml_content = _yaml("  on_success:\n    - team-chat\n")
            await other.commit()

    snapshot = await _run_stages(sf, monkeypatch, ids["build"], on_step=edit_pipeline)
    await build_executor._send_notifications(snapshot, sf)

    assert _first_lines(slack) == [
        "Build #428 of deploy-staging started", "Build #428 of deploy-staging succeeded"]


async def _agent(sf, build_id):
    from app.models.agent import Agent

    agent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with sf() as db:
        db.add(Agent(id=agent_id, name="a1", status="online", enabled=True,
                     connected_at=now, last_seen_at=now, current_build_id=build_id))
        await db.commit()
    return agent_id


async def test_the_end_message_waits_for_a_start_message_still_being_sent(sf, fake_redis, monkeypatch):
    """"Started" must not arrive after "failed"."""
    from app.services import build_executor
    from app.services.build_notifications import BuildSnapshot

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")
    agent_id = await _agent(sf, ids["build"])
    order = []

    def snapshot(status):
        return BuildSnapshot(
            build_id=ids["build"], pipeline_id=ids["pipeline"], number=428, failed_stage="",
            failed_step="", failed_step_id=None, secrets={}, env_vars={}, builtins={},
            status=status,
        )

    async def fake_send(snap, session_factory):
        order.append(f"{snap.status} begins")
        if snap.status == "running":
            await asyncio.sleep(0.1)
        order.append(f"{snap.status} ends")

    async def fake_stages(**kwargs):
        build_executor._send_in_background(snapshot("running"), sf)
        return snapshot("failed")

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(build_executor, "_send_notifications", fake_send)
    monkeypatch.setattr(build_executor, "_run_build_stages", fake_stages)
    monkeypatch.setattr(build_executor, "release_agent", noop)
    monkeypatch.setattr(build_executor, "send_build_finished", noop)
    monkeypatch.setattr(build_executor, "dispatch_pending_builds", noop)

    await build_executor.execute_build(ids["build"], session_factory=sf, claimed_agent_id=agent_id)

    assert order == ["running begins", "running ends", "failed begins", "failed ends"]
    assert ids["build"] not in build_executor._background_sends, "finished sends are forgotten"


async def test_a_send_in_flight_is_finished_even_when_the_executor_crashes(sf, fake_redis, monkeypatch):
    """Left pending, the task would live as long as the worker process, and
    its database session with it."""
    from app.services import build_executor
    from app.services.build_notifications import BuildSnapshot

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")
    agent_id = await _agent(sf, ids["build"])
    order = []

    async def fake_send(snap, session_factory):
        order.append("send begins")
        await asyncio.sleep(0.1)
        order.append("send ends")

    async def crashing_stages(**kwargs):
        build_executor._send_in_background(
            BuildSnapshot(
                build_id=ids["build"], pipeline_id=ids["pipeline"], number=428, failed_stage="",
                failed_step="", failed_step_id=None, secrets={}, env_vars={}, builtins={},
                status="running",
            ),
            sf,
        )
        await asyncio.sleep(0)  # the send gets going
        raise ConnectionError("redis went away")

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(build_executor, "_send_notifications", fake_send)
    monkeypatch.setattr(build_executor, "_run_build_stages", crashing_stages)
    monkeypatch.setattr(build_executor, "release_agent", noop)
    monkeypatch.setattr(build_executor, "send_build_finished", noop)
    monkeypatch.setattr(build_executor, "dispatch_pending_builds", noop)

    with pytest.raises(ConnectionError):
        await build_executor.execute_build(
            ids["build"], session_factory=sf, claimed_agent_id=agent_id)

    assert order == ["send begins", "send ends"]
    assert ids["build"] not in build_executor._background_sends


async def test_messages_of_one_build_go_out_in_the_order_things_happened(sf, monkeypatch):
    """A pipeline whose first step is an approval: "started" must not arrive
    after "waiting for approval", however slow the first send is."""
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_waiting:\n    - team-chat\n",
                      step_type="wait_input")
    sent = []

    async def slack_slow_to_say_started(config, message, recipient):
        first_line = message.splitlines()[0]
        if first_line.endswith("started"):
            await asyncio.sleep(0.15)
        sent.append(first_line)

    monkeypatch.setattr("app.services.notification_service._send_slack", slack_slow_to_say_started)

    await _run_stages(sf, monkeypatch, ids["build"])

    assert sent == [
        "Build #428 of deploy-staging started",
        "Build #428 of deploy-staging is waiting for approval",
    ]


@pytest.mark.parametrize("build_status", ["cancelled", "success", "failed", "running"])
async def test_a_build_that_is_not_pending_is_not_run_and_sends_nothing(sf, slack, fake_redis,
                                                                        monkeypatch, build_status):
    """For example a build cancelled, or replaced by a newer trigger, before it started."""
    from app.services import build_executor

    block = "".join(f"  {event}:\n    - team-chat\n"
                    for event in ("on_start", "on_cancelled", "on_complete"))
    ids = await _seed(sf, block, build_status=build_status)

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(build_executor, "release_agent", noop)

    await build_executor.execute_build(ids["build"], session_factory=sf, claimed_agent_id=uuid.uuid4())

    assert slack == []


# ── nothing here may disturb the build ──────────────────────────────────

async def test_a_send_that_raises_never_changes_the_build(sf, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_waiting:\n    - team-chat\n",
                      step_type="wait_input")

    async def explode(*args, **kwargs):
        raise RuntimeError("notification subsystem is down")

    monkeypatch.setattr(build_executor, "send_build_notifications", explode)

    snapshot = await _run_stages(sf, monkeypatch, ids["build"])

    async with sf() as db:
        assert (await db.get(Build, ids["build"])).status == "success"
    assert snapshot.status == "success"


async def test_a_pipeline_whose_notifications_cannot_be_read_still_builds(sf, slack, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor

    ids = await _seed(sf, "  on_start:\n    - team-chat\n", step_type="wait_input")

    def explode(yaml_content):
        raise RuntimeError("cannot parse")

    monkeypatch.setattr(build_executor, "notifications_by_event", explode)

    await _run_stages(sf, monkeypatch, ids["build"])

    async with sf() as db:
        assert (await db.get(Build, ids["build"])).status == "success"
    assert slack == []


async def test_a_snapshot_that_cannot_be_taken_sends_nothing_and_the_build_goes_on(sf, slack, monkeypatch):
    from app.models.build import Build
    from app.services import build_executor
    from app.services.build_notifications import BuildSnapshot

    ids = await _seed(sf, "  on_start:\n    - team-chat\n")

    def explode(*args, **kwargs):
        raise RuntimeError("no snapshot")

    monkeypatch.setattr(BuildSnapshot, "capture", explode)

    assert await _run_stages(sf, monkeypatch, ids["build"]) is None
    async with sf() as db:
        assert (await db.get(Build, ids["build"])).status == "success"
    assert slack == []


# ── where problems are logged ───────────────────────────────────────────

async def _system_lines(sf, step_id):
    from app.models.build import LogChunk

    async with sf() as db:
        return [c.content for c in (await db.execute(
            select(LogChunk).where(LogChunk.step_id == step_id, LogChunk.stream == "system")
        )).scalars()]


async def test_a_problem_with_a_start_notification_is_logged_under_the_first_step(
        sf, slack, fake_redis, monkeypatch):
    ids = await _seed(sf, "  on_start:\n    - no-such-channel\n    - team-chat\n")

    await _run_stages(sf, monkeypatch, ids["build"])

    lines = await _system_lines(sf, ids["step"])
    assert any("Start notification not sent: channel 'no-such-channel' was not found" in line
               for line in lines), lines
    assert len(slack) == 1, "the entry after the bad one is still sent"
    assert fake_redis.published, "the log line is also published live"


async def test_a_problem_with_an_end_notification_is_logged_under_the_last_step_that_ran(
        sf, slack, fake_redis, monkeypatch):
    from app.services import build_executor

    ids = await _seed(sf, "  on_success:\n    - no-such-channel\n")
    snapshot = await _run_stages(sf, monkeypatch, ids["build"])

    await build_executor._send_notifications(snapshot, sf)

    assert snapshot.log_step_id == ids["step"]
    lines = await _system_lines(sf, ids["step"])
    assert any("Success notification not sent: channel 'no-such-channel' was not found" in line
               for line in lines), lines


async def test_no_redis_client_is_opened_when_there_is_nothing_to_report(sf, slack, monkeypatch):
    from app.services import build_executor

    def no_redis(*args, **kwargs):
        raise AssertionError("opened a Redis client")

    monkeypatch.setattr(build_executor.aioredis, "from_url", no_redis)
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_success:\n    - team-chat\n")

    snapshot = await _run_stages(sf, monkeypatch, ids["build"])
    await build_executor._send_notifications(snapshot, sf)

    assert len(slack) == 2
