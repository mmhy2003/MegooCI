"""Every notification event: which entries a build's moment calls for, what
they say, and that they are sent."""
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from tests._failure_notifications import builtins_for, load_build, seed_build, seed_channel
from tests._rbac import build_inmemory_factory


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


@pytest.fixture
def providers(monkeypatch):
    """Replace the three network senders; record what each was asked to send."""
    calls = {"slack": [], "telegram": [], "email": []}

    async def fake_slack(config, message, recipient):
        calls["slack"].append({"message": message, "recipient": recipient})

    async def fake_telegram(config, message, recipient):
        calls["telegram"].append({"message": message, "recipient": recipient})

    def fake_email(config, to_email, subject, body):
        calls["email"].append({"to": to_email, "subject": subject, "message": body})

    monkeypatch.setattr("app.services.notification_service._send_slack", fake_slack)
    monkeypatch.setattr("app.services.notification_service._send_telegram", fake_telegram)
    monkeypatch.setattr("app.services.notification_service._send_email_sync", fake_email)
    return calls


def _yaml(block: str) -> str:
    return (
        "name: deploy-staging\n"
        "notifications:\n" + block +
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )


def N(channel, **fields):
    from app.services.build_notifications import Notification

    return Notification(channel=channel, **fields)


# ── reading the block ───────────────────────────────────────────────────

def test_reader_returns_the_entries_of_each_event_that_has_any():
    from app.services.build_notifications import notifications_by_event

    found = notifications_by_event(_yaml(
        "  on_complete:\n"
        "    - team-chat\n"
        "  on_start:\n"
        "    - team-chat\n"
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "  on_done:\n"            # not an event
        "    - team-chat\n"
        "  on_fixed: nope\n"      # not a list
        "  on_waiting:\n"         # nothing usable in it
        "    - 42\n"
    ))

    assert found == {
        "on_start": [N("team-chat"), N("ops-email", recipient="oncall@example.com")],
        "on_complete": [N("team-chat")],
    }


@pytest.mark.parametrize("yaml_content", [None, "", "name: [unclosed\n", "- a list\n",
                                          "notifications: team-chat\n", "name: x\nstages: []\n"])
def test_reader_is_safe_on_missing_or_malformed_input(yaml_content):
    from app.services.build_notifications import notifications_by_event

    assert notifications_by_event(yaml_content) == {}


# ── which events a moment matches ───────────────────────────────────────

@pytest.mark.parametrize(
    "status, waiting, fixed, expected",
    [
        ("running", False, False, ("on_start",)),
        ("running", True, False, ("on_waiting",)),
        ("success", False, False, ("on_success", "on_complete")),
        ("success", False, True, ("on_fixed", "on_success", "on_complete")),
        ("failed", False, False, ("on_failure", "on_complete")),
        ("failed", False, True, ("on_failure", "on_complete")),
        ("cancelled", False, False, ("on_cancelled", "on_complete")),
        ("pending", False, False, ()),
    ],
)
def test_matching_events_most_specific_first(status, waiting, fixed, expected):
    from app.services.build_notifications import matching_events

    assert matching_events(status, waiting=waiting, fixed=fixed) == expected


# ── one message per destination ─────────────────────────────────────────

def _select(by_event, *events):
    from app.services.build_notifications import select_notifications

    return select_notifications(by_event, events)


def test_a_channel_under_two_matching_events_gets_the_more_specific_one():
    custom = N("team-chat", message="It broke")
    by_event = {"on_failure": [custom], "on_complete": [N("team-chat")]}

    assert _select(by_event, "on_failure", "on_complete") == [("on_failure", custom)]
    assert _select(by_event, "on_success", "on_complete") == [("on_complete", N("team-chat"))]


def test_fixed_wins_over_success_which_wins_over_complete():
    by_event = {
        "on_fixed": [N("team-chat")],
        "on_success": [N("team-chat"), N("deploys")],
        "on_complete": [N("team-chat"), N("deploys"), N("audit")],
    }

    assert _select(by_event, "on_fixed", "on_success", "on_complete") == [
        ("on_fixed", N("team-chat")), ("on_success", N("deploys")), ("on_complete", N("audit")),
    ]
    assert _select(by_event, "on_success", "on_complete") == [
        ("on_success", N("team-chat")), ("on_success", N("deploys")), ("on_complete", N("audit")),
    ]


def test_the_same_channel_with_another_recipient_is_another_destination():
    by_event = {
        "on_failure": [N("ops-email", recipient="oncall@example.com")],
        "on_complete": [N("ops-email", recipient="team@example.com"), N("ops-email"),
                        N("ops-email", recipient="oncall@example.com")],
    }

    assert _select(by_event, "on_failure", "on_complete") == [
        ("on_failure", N("ops-email", recipient="oncall@example.com")),
        ("on_complete", N("ops-email", recipient="team@example.com")),
        ("on_complete", N("ops-email")),
    ]


def test_a_destination_listed_twice_under_one_event_is_sent_twice():
    by_event = {"on_complete": [N("team-chat", message="one"), N("team-chat", message="two")]}

    assert _select(by_event, "on_success", "on_complete") == [
        ("on_complete", N("team-chat", message="one")),
        ("on_complete", N("team-chat", message="two")),
    ]


def test_events_that_do_not_match_are_never_used():
    by_event = {"on_fixed": [N("team-chat")], "on_start": [N("team-chat")],
                "on_failure": [N("team-chat")]}

    assert _select(by_event, "on_success", "on_complete") == []


# ── default wording ─────────────────────────────────────────────────────

def _values(status, **build):
    return {
        "build": {"number": "12", "branch": "main", "commit": "3f2a9c1d5e6f7a8b", "status": status,
                  "failed_stage": "", "failed_step": "", "waiting_stage": "", "waiting_step": "",
                  "url": "https://ci.example.com/builds/abc", **build},
        "pipeline": {"name": "deploy-production"},
        "project": {"name": "Shop"},
    }


@pytest.mark.parametrize(
    "event, status, subject",
    [
        ("on_start", "running", "Build #12 of deploy-production started"),
        ("on_waiting", "running", "Build #12 of deploy-production is waiting for approval"),
        ("on_success", "success", "Build #12 of deploy-production succeeded"),
        ("on_fixed", "success", "Build #12 of deploy-production is fixed"),
        ("on_failure", "failed", "Build #12 of deploy-production failed"),
        ("on_cancelled", "cancelled", "Build #12 of deploy-production was cancelled"),
        ("on_complete", "success", "Build #12 of deploy-production succeeded"),
        ("on_complete", "failed", "Build #12 of deploy-production failed"),
        ("on_complete", "cancelled", "Build #12 of deploy-production was cancelled"),
    ],
)
def test_default_subject_for_every_event(event, status, subject):
    from app.services.build_notifications import default_message, default_subject

    values = _values(status)
    assert default_subject(values, event) == subject
    assert default_message(values, event) == (
        f"{subject}\n"
        "Project: Shop | Branch: main | Commit: 3f2a9c1\n"
        "https://ci.example.com/builds/abc"
    )


def test_default_message_says_where_a_build_is_waiting():
    from app.services.build_notifications import default_message

    values = _values("running", waiting_stage="release", waiting_step="approve production")
    assert default_message(values, "on_waiting") == (
        "Build #12 of deploy-production is waiting for approval\n"
        "Project: Shop | Branch: main | Commit: 3f2a9c1\n"
        "Waiting at: stage \"release\", step \"approve production\"\n"
        "https://ci.example.com/builds/abc"
    )


def test_on_complete_for_a_failed_build_says_where_it_failed():
    from app.services.build_notifications import default_message

    values = _values("failed", failed_stage="deploy", failed_step="apply manifests")
    assert "Failed at: stage \"deploy\", step \"apply manifests\"" in default_message(values, "on_complete")


# ── the snapshot ────────────────────────────────────────────────────────

async def _two_step_build(sf, *, build_status, statuses, started, yaml_content=None):
    """A build whose single stage has two steps. Returns ids with 'steps'."""
    from app.models.build import Step

    # Project names are unique, and some tests seed more than one build.
    kwargs = {"project_name": f"Project {uuid.uuid4().hex[:8]}"}
    if yaml_content:
        kwargs["yaml_content"] = yaml_content
    async with sf() as db:
        ids = await seed_build(db, build_status=build_status, step_status=statuses[0], **kwargs)
        first = await db.get(Step, ids["step"])
        second = Step(id=uuid.uuid4(), stage_id=ids["stage"], name="smoke test", step_type="run",
                      status=statuses[1], sort_order=1)
        db.add(second)
        now = datetime.now(timezone.utc)
        first.started_at = now if started[0] else None
        second.started_at = now if started[1] else None
        await db.commit()
    return {**ids, "steps": [ids["step"], second.id]}


async def _capture(sf, build_id, **kwargs):
    from app.services.build_notifications import BuildSnapshot

    async with sf() as db:
        build = await load_build(db, build_id)
        if kwargs.pop("waiting_at_second_step", False):
            stage = build.stages[0]
            kwargs["waiting"] = (stage, sorted(stage.steps, key=lambda s: s.sort_order)[1])
        return BuildSnapshot.capture(build, {}, {}, builtins_for(build), **kwargs)


async def test_snapshot_of_a_build_that_just_started(sf):
    ids = await _two_step_build(sf, build_status="running", statuses=("pending", "pending"),
                                started=(False, False))

    snapshot = await _capture(sf, ids["build"])

    assert snapshot.status == "running" and snapshot.branch == "develop"
    assert (snapshot.failed_stage, snapshot.failed_step, snapshot.failed_step_id) == ("", "", None)
    assert (snapshot.waiting_stage, snapshot.waiting_step) == ("", "")
    assert snapshot.log_step_id == ids["steps"][0], "problems are logged under the first step"


async def test_snapshot_of_a_build_waiting_for_approval(sf):
    ids = await _two_step_build(sf, build_status="running", statuses=("success", "running"),
                                started=(True, True))

    snapshot = await _capture(sf, ids["build"], waiting_at_second_step=True)

    assert snapshot.status == "running"
    assert (snapshot.waiting_stage, snapshot.waiting_step) == ("deploy", "smoke test")
    assert snapshot.log_step_id == ids["steps"][1]


async def test_snapshot_of_a_finished_build_logs_under_the_last_step_that_ran(sf):
    done = await _two_step_build(sf, build_status="success", statuses=("success", "success"),
                                 started=(True, True))
    cancelled = await _two_step_build(sf, build_status="cancelled",
                                      statuses=("success", "cancelled"), started=(True, False))

    assert (await _capture(sf, done["build"])).log_step_id == done["steps"][1]
    snapshot = await _capture(sf, cancelled["build"])
    assert snapshot.status == "cancelled" and snapshot.log_step_id == cancelled["steps"][0]


async def test_snapshot_of_a_failed_build_logs_under_the_failed_step(sf):
    ids = await _two_step_build(sf, build_status="failed", statuses=("failed", "pending"),
                                started=(True, False))

    snapshot = await _capture(sf, ids["build"])

    assert snapshot.status == "failed"
    assert (snapshot.failed_stage, snapshot.failed_step) == ("deploy", "apply manifests")
    assert snapshot.failed_step_id == snapshot.log_step_id == ids["steps"][0]


async def test_snapshot_status_can_be_given_and_a_missing_branch_is_none(sf):
    async with sf() as db:
        ids = await seed_build(db, build_status="pending", step_status="pending", branch=None)
        await db.commit()

    snapshot = await _capture(sf, ids["build"], status="running")

    assert snapshot.status == "running" and snapshot.branch is None


# ── "fixed" ─────────────────────────────────────────────────────────────

async def _history(sf, *earlier, branch="main"):
    """A pipeline whose build #428 is on *branch*, with earlier builds given
    as (number, status, branch). Returns a snapshot of #428."""
    from app.models.build import Build

    async with sf() as db:
        ids = await seed_build(db, build_status="success", step_status="success", branch=branch,
                               project_name=f"Project {uuid.uuid4().hex[:8]}")
        for number, status, earlier_branch in earlier:
            db.add(Build(id=uuid.uuid4(), pipeline_id=ids["pipeline"], number=number,
                         status=status, trigger_type="manual", branch=earlier_branch))
        await db.commit()
    return await _capture(sf, ids["build"])


async def _was_fixed(sf, *earlier, branch="main"):
    from app.services.build_notifications import previous_build_failed

    snapshot = await _history(sf, *earlier, branch=branch)
    async with sf() as db:
        return await previous_build_failed(db, snapshot)


async def test_success_after_a_failure_is_a_fix(sf):
    assert await _was_fixed(sf, (426, "success", "main"), (427, "failed", "main")) is True


async def test_success_after_a_success_is_not_a_fix(sf):
    assert await _was_fixed(sf, (426, "failed", "main"), (427, "success", "main")) is False


async def test_a_failure_on_another_branch_does_not_count(sf):
    assert await _was_fixed(sf, (426, "success", "main"), (427, "failed", "feature/x")) is False


async def test_cancelled_and_unfinished_builds_in_between_are_skipped(sf):
    assert await _was_fixed(
        sf, (424, "failed", "main"), (425, "cancelled", "main"), (426, "running", "main"),
        (427, "pending", "main"),
    ) is True


async def test_the_first_build_of_a_pipeline_or_branch_is_not_a_fix(sf):
    assert await _was_fixed(sf) is False
    assert await _was_fixed(sf, (427, "failed", "main"), branch="release") is False


async def test_builds_without_a_branch_are_one_branch(sf):
    assert await _was_fixed(sf, (426, "failed", None), (427, "failed", "main"), branch=None) is True
    assert await _was_fixed(sf, (427, "failed", ""), branch=None) is True
    assert await _was_fixed(sf, (427, "failed", "main"), branch=None) is False


async def test_a_later_build_number_is_not_a_previous_build(sf):
    assert await _was_fixed(sf, (429, "failed", "main")) is False


async def test_another_pipelines_failure_does_not_count(sf):
    from app.services.build_notifications import previous_build_failed

    async with sf() as db:
        other = await seed_build(db, build_status="failed", branch="main", project_name="Other")
        other_build = await load_build(db, other["build"])
        other_build.number = 427
        await db.commit()
    snapshot = await _history(sf)

    async with sf() as db:
        assert await previous_build_failed(db, snapshot) is False


# ── sending ─────────────────────────────────────────────────────────────

async def _seed(sf, block, *, build_status, channels=("team-chat",), **kwargs):
    step_status = {"running": "running", "cancelled": "cancelled"}.get(build_status, build_status)
    async with sf() as db:
        for name in channels:
            kind = "email" if "email" in name else "telegram" if "telegram" in name else "slack"
            await seed_channel(db, name, kind)
        ids = await seed_build(db, yaml_content=_yaml(block), build_status=build_status,
                               step_status=step_status, **kwargs)
        await db.commit()
    return ids


async def _send(sf, build_id, **capture):
    from app.services.build_notifications import send_build_notifications

    reports = []

    async def report(text):
        reports.append(text)

    snapshot = await _capture(sf, build_id, **capture)
    async with sf() as db:
        sent = await send_build_notifications(db, snapshot, report=report)
    return sent, reports


async def test_on_start_is_sent_for_a_build_that_started(sf, providers):
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_complete:\n    - team-chat\n",
                      build_status="running")

    sent, reports = await _send(sf, ids["build"])

    assert (sent, reports) == (1, [])
    assert providers["slack"][0]["message"].splitlines()[0] == "Build #428 of deploy-staging started"


async def test_on_waiting_is_sent_with_the_step_that_needs_approval(sf, providers):
    block = (
        "  on_start:\n    - team-chat\n"
        "  on_waiting:\n"
        "    - team-chat\n"
        "    - channel: team-chat\n"
        "      message: \"${{ build.status }} at ${{ build.waiting_stage }} / "
        "${{ build.waiting_step }} [${{ build.failed_step }}]\"\n"
    )
    ids = await _two_step_build(sf, build_status="running", statuses=("success", "running"),
                                started=(True, True), yaml_content=_yaml(block))
    async with sf() as db:
        await seed_channel(db, "team-chat")
        await db.commit()

    sent, _ = await _send(sf, ids["build"], waiting_at_second_step=True)

    assert sent == 2
    default, custom = [call["message"] for call in providers["slack"]]
    assert default.splitlines()[0] == "Build #428 of deploy-staging is waiting for approval"
    assert "Waiting at: stage \"deploy\", step \"smoke test\"" in default
    assert custom == "running at deploy / smoke test []"


@pytest.mark.parametrize(
    "build_status, event, ending",
    [("success", "on_success", "succeeded"), ("cancelled", "on_cancelled", "was cancelled"),
     ("failed", "on_failure", "failed")],
)
async def test_each_outcome_sends_its_own_event(sf, providers, build_status, event, ending):
    others = "".join(f"  {e}:\n    - other-chat\n"
                     for e in ("on_success", "on_cancelled", "on_failure") if e != event)
    ids = await _seed(sf, f"  {event}:\n    - team-chat\n" + others, build_status=build_status,
                      channels=("team-chat", "other-chat"))

    sent, _ = await _send(sf, ids["build"])

    assert sent == 1
    assert providers["slack"][0]["message"].splitlines()[0] == f"Build #428 of deploy-staging {ending}"


@pytest.mark.parametrize("build_status", ["success", "failed", "cancelled"])
async def test_on_complete_is_sent_for_every_outcome_with_the_status(sf, providers, build_status):
    block = (
        "  on_complete:\n"
        "    - channel: team-chat\n"
        "      message: \"${{ pipeline.name }} #${{ build.number }}: ${{ build.status }}\"\n"
    )
    ids = await _seed(sf, block, build_status=build_status)

    await _send(sf, ids["build"])

    assert [call["message"] for call in providers["slack"]] == [f"deploy-staging #428: {build_status}"]


async def test_end_events_are_not_sent_while_the_build_is_running(sf, providers):
    block = "".join(f"  {event}:\n    - team-chat\n" for event in
                    ("on_success", "on_fixed", "on_failure", "on_cancelled", "on_complete"))
    ids = await _seed(sf, block, build_status="running")

    assert await _send(sf, ids["build"]) == (0, [])
    assert providers["slack"] == []


async def test_start_and_waiting_are_not_sent_when_the_build_ends(sf, providers):
    ids = await _seed(sf, "  on_start:\n    - team-chat\n  on_waiting:\n    - team-chat\n",
                      build_status="success")

    assert await _send(sf, ids["build"]) == (0, [])


OVERLAP = (
    "  on_failure:\n"
    "    - channel: team-chat\n"
    "      message: \"FIRE at ${{ build.failed_step }}\"\n"
    "  on_complete:\n"
    "    - team-chat\n"
)


async def test_a_failed_build_sends_one_message_to_a_channel_under_two_events(sf, providers):
    ids = await _seed(sf, OVERLAP, build_status="failed")

    sent, _ = await _send(sf, ids["build"])

    assert sent == 1
    assert [call["message"] for call in providers["slack"]] == ["FIRE at apply manifests"]


async def test_the_general_event_is_used_when_the_specific_one_does_not_match(sf, providers):
    ids = await _seed(sf, OVERLAP, build_status="success")

    await _send(sf, ids["build"])

    assert len(providers["slack"]) == 1
    assert providers["slack"][0]["message"].startswith("Build #428 of deploy-staging succeeded")


FIXED = (
    "  on_fixed:\n    - team-chat\n"
    "  on_success:\n    - team-chat\n    - deploys\n"
    "  on_complete:\n    - team-chat\n"
)


async def _earlier_build(sf, pipeline_id, status):
    from app.models.build import Build

    async with sf() as db:
        db.add(Build(id=uuid.uuid4(), pipeline_id=pipeline_id, number=427, status=status,
                     trigger_type="manual", branch="develop"))
        await db.commit()


async def test_a_fix_sends_the_fixed_message_once_and_success_to_the_others(sf, providers):
    ids = await _seed(sf, FIXED, build_status="success", channels=("team-chat", "deploys"))
    await _earlier_build(sf, ids["pipeline"], "failed")

    sent, _ = await _send(sf, ids["build"])

    assert sent == 2
    assert [call["message"].splitlines()[0] for call in providers["slack"]] == [
        "Build #428 of deploy-staging is fixed", "Build #428 of deploy-staging succeeded",
    ]


async def test_an_ordinary_success_never_sends_the_fixed_message(sf, providers):
    ids = await _seed(sf, FIXED, build_status="success", channels=("team-chat", "deploys"))
    await _earlier_build(sf, ids["pipeline"], "success")

    await _send(sf, ids["build"])

    assert [call["message"].splitlines()[0] for call in providers["slack"]] == [
        "Build #428 of deploy-staging succeeded"] * 2


async def test_the_previous_build_is_not_looked_up_unless_on_fixed_is_listed(sf, providers, monkeypatch):
    from app.services import build_notifications

    async def never(db, snapshot):
        raise AssertionError("looked up the previous build")

    monkeypatch.setattr(build_notifications, "previous_build_failed", never)
    ids = await _seed(sf, "  on_success:\n    - team-chat\n", build_status="success")

    assert (await _send(sf, ids["build"]))[0] == 1


async def test_a_failed_build_never_looks_up_the_previous_one(sf, providers, monkeypatch):
    from app.services import build_notifications

    async def never(db, snapshot):
        raise AssertionError("looked up the previous build")

    monkeypatch.setattr(build_notifications, "previous_build_failed", never)
    ids = await _seed(sf, FIXED + "  on_failure:\n    - team-chat\n", build_status="failed")

    assert (await _send(sf, ids["build"]))[0] == 1


@pytest.mark.parametrize(
    "build_status, event, label",
    [
        ("running", "on_start", "Start notification"),
        ("success", "on_success", "Success notification"),
        ("cancelled", "on_cancelled", "Cancellation notification"),
        ("success", "on_complete", "Completion notification"),
    ],
)
async def test_problems_are_reported_under_the_name_of_the_event(sf, providers, build_status, event, label):
    async with sf() as db:
        await seed_channel(db, "off-chat", enabled=False)
        await db.commit()
    ids = await _seed(sf, f"  {event}:\n    - nope\n    - off-chat\n    - team-chat\n",
                      build_status=build_status)

    sent, reports = await _send(sf, ids["build"])

    assert sent == 1, "the good entry is still sent"
    assert reports == [
        f"{label} not sent: channel 'nope' was not found.",
        f"{label} not sent: channel 'off-chat' is disabled.",
    ]


async def test_a_fixed_build_reports_problems_as_a_fixed_notification(sf, providers):
    ids = await _seed(sf, "  on_fixed:\n    - nope\n", build_status="success")
    await _earlier_build(sf, ids["pipeline"], "failed")

    assert await _send(sf, ids["build"]) == (
        0, ["Fixed notification not sent: channel 'nope' was not found."])


async def test_a_waiting_build_reports_problems_as_an_approval_notification(sf, providers):
    ids = await _two_step_build(
        sf, build_status="running", statuses=("success", "running"), started=(True, True),
        yaml_content=_yaml("  on_waiting:\n    - nope\n"))

    assert await _send(sf, ids["build"], waiting_at_second_step=True) == (
        0, ["Approval notification not sent: channel 'nope' was not found."])


async def test_built_in_values_are_escaped_for_telegram_on_a_new_event(sf, providers):
    block = (
        "  on_success:\n"
        "    - telegram-chat\n"
        "    - channel: telegram-chat\n"
        "      message: \"<b>done</b> on ${{ build.branch }}\"\n"
    )
    ids = await _seed(sf, block, build_status="success", channels=("telegram-chat",),
                      branch="fix/<script>&co")

    await _send(sf, ids["build"])

    default, custom = [call["message"] for call in providers["telegram"]]
    assert "Branch: fix/&lt;script&gt;&amp;co" in default
    assert custom == "<b>done</b> on fix/&lt;script&gt;&amp;co"


async def test_an_email_entry_gets_the_events_default_subject(sf, providers):
    block = "  on_cancelled:\n    - channel: ops-email\n      recipient: oncall@example.com\n"
    ids = await _seed(sf, block, build_status="cancelled", channels=("ops-email",))

    await _send(sf, ids["build"])

    assert providers["email"][0]["to"] == "oncall@example.com"
    assert providers["email"][0]["subject"] == "Build #428 of deploy-staging was cancelled"


async def test_a_delivery_is_recorded_for_a_new_event(sf, providers):
    from sqlalchemy import select

    from app.models.notification import NotificationDelivery

    ids = await _seed(sf, "  on_start:\n    - team-chat\n", build_status="running")

    await _send(sf, ids["build"])

    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.build_id == ids["build"] and row.status == "sent"
