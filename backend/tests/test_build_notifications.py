"""Reading the notifications block, building the message, and sending it."""
import os

import pytest
import pytest_asyncio
from sqlalchemy import select

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from tests._failure_notifications import (
    PIPELINE_YAML,
    builtins_for,
    load_build,
    seed_build,
    seed_channel,
)
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


def _yaml(on_failure_block: str) -> str:
    return (
        "name: deploy-staging\n"
        "notifications:\n"
        "  on_failure:\n" + on_failure_block +
        "stages:\n  - name: deploy\n    steps:\n      - run: ./deploy.sh\n"
    )


async def _send(sf, build_id, **kwargs):
    from app.services.build_notifications import send_failure_notifications

    reports = []

    async def report(text):
        reports.append(text)

    async with sf() as db:
        build = await load_build(db, build_id)
        sent = await send_failure_notifications(
            db, build, secrets=kwargs.get("secrets", {}), env_vars=kwargs.get("env_vars", {}),
            builtins=builtins_for(build), report=report,
        )
    return sent, reports


# ── reading the block ───────────────────────────────────────────────────

def test_short_and_full_entries_normalize_to_the_same_shape():
    from app.services.build_notifications import FailureNotification, failure_notifications

    entries = failure_notifications(_yaml(
        "    - deploy-alerts\n"
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "      subject: Deploy failed\n"
        "      message: It broke\n"
    ))
    assert entries == [
        FailureNotification(channel="deploy-alerts"),
        FailureNotification(channel="ops-email", message="It broke",
                            subject="Deploy failed", recipient="oncall@example.com"),
    ]


@pytest.mark.parametrize(
    "yaml_content",
    [
        None,
        "",
        "name: x\nstages: []\n",                             # no block
        "name: [unclosed\n",                                 # invalid YAML
        "- just\n- a list\n",                                # not a mapping
        "notifications: deploy-alerts\n",                    # block not a mapping
        "notifications:\n  on_failure: deploy-alerts\n",     # not a list
        "notifications:\n  on_success:\n    - a\n",          # other event only
    ],
)
def test_reader_returns_nothing_for_missing_or_malformed_input(yaml_content):
    from app.services.build_notifications import failure_notifications

    assert failure_notifications(yaml_content) == []


def test_reader_skips_malformed_entries_and_keeps_good_ones():
    from app.services.build_notifications import failure_notifications

    entries = failure_notifications(_yaml(
        "    - 42\n"
        "    - \"\"\n"
        "    - recipient: nobody\n"
        "    - good-channel\n"
    ))
    assert [e.channel for e in entries] == ["good-channel"]


# ── the message ─────────────────────────────────────────────────────────

VALUES = {
    "build": {"number": "428", "branch": "develop", "commit": "3f2a9c1d5e6f7a8b",
              "failed_stage": "deploy", "failed_step": "apply manifests",
              "url": "https://ci.example.com/builds/abc"},
    "pipeline": {"name": "deploy-staging-inbox"},
    "project": {"name": "Inbox Staging"},
}


def test_default_message_and_subject():
    from app.services.build_notifications import default_message, default_subject

    assert default_subject(VALUES) == "Build #428 of deploy-staging-inbox failed"
    assert default_message(VALUES) == (
        "Build #428 of deploy-staging-inbox failed\n"
        "Project: Inbox Staging | Branch: develop | Commit: 3f2a9c1\n"
        "Failed at: stage \"deploy\", step \"apply manifests\"\n"
        "https://ci.example.com/builds/abc"
    )


def test_default_message_leaves_out_what_the_build_does_not_have():
    from app.services.build_notifications import default_message

    values = {
        "build": {"number": "7", "branch": "", "commit": "", "failed_stage": "",
                  "failed_step": "", "url": "https://ci.example.com/builds/x"},
        "pipeline": {"name": "nightly"},
        "project": {},
    }
    assert default_message(values) == (
        "Build #7 of nightly failed\n"
        "https://ci.example.com/builds/x"
    )


# ── sending ─────────────────────────────────────────────────────────────

async def test_sends_the_default_message_and_records_a_delivery(sf, providers, monkeypatch):
    from app.models.notification import NotificationDelivery

    monkeypatch.setenv("MEGOOCI_PUBLIC_URL", "https://ci.example.com/")
    from app.config import get_settings
    get_settings.cache_clear()

    async with sf() as db:
        channel_id = await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])
    get_settings.cache_clear()

    assert sent == 1 and reports == []
    message = providers["slack"][0]["message"]
    assert message == (
        "Build #428 of deploy-staging failed\n"
        "Project: Inbox Staging | Branch: develop | Commit: 3f2a9c1\n"
        "Failed at: stage \"deploy\", step \"apply manifests\"\n"
        f"https://ci.example.com/builds/{ids['build']}"
    )
    async with sf() as db:
        rows = (await db.execute(select(NotificationDelivery))).scalars().all()
    assert len(rows) == 1
    assert rows[0].channel_id == channel_id
    assert rows[0].build_id == ids["build"]
    assert rows[0].status == "sent"


async def test_custom_message_subject_and_recipient(sf, providers):
    yaml_content = _yaml(
        "    - channel: ops-email\n"
        "      recipient: oncall@example.com\n"
        "      subject: \"${{ pipeline.name }} is ${{ build.status }}\"\n"
        "      message: |\n"
        "        #${{ build.number }} failed at ${{ build.failed_stage }} / ${{ build.failed_step }}\n"
        "        token ${{ secrets.NOTE }} env ${{ env.REGION }}\n"
        "        ${{ build.url }}\n"
    )
    async with sf() as db:
        await seed_channel(db, "ops-email", "email")
        ids = await seed_build(db, yaml_content=yaml_content)
        await db.commit()

    sent, reports = await _send(sf, ids["build"], secrets={"NOTE": "s3"}, env_vars={"REGION": "eu"})

    assert sent == 1 and reports == []
    mail = providers["email"][0]
    assert mail["to"] == "oncall@example.com"
    assert mail["subject"] == "deploy-staging is failed"
    assert mail["message"].startswith("#428 failed at deploy / apply manifests\ntoken s3 env eu\n")
    assert mail["message"].rstrip().endswith(f"/builds/{ids['build']}")


async def test_each_entry_is_sent_independently(sf, providers):
    yaml_content = _yaml(
        "    - deploy-alerts\n"
        "    - missing-channel\n"
        "    - disabled-channel\n"
        "    - channel: tg-ops\n"
        "      recipient: \"-200\"\n"
    )
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        await seed_channel(db, "disabled-channel", enabled=False)
        await seed_channel(db, "tg-ops", "telegram")
        ids = await seed_build(db, yaml_content=yaml_content)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 2
    assert len(providers["slack"]) == 1
    assert providers["telegram"][0]["recipient"] == "-200"
    assert reports == [
        "Failure notification not sent: channel 'missing-channel' was not found.",
        "Failure notification not sent: channel 'disabled-channel' is disabled.",
    ]


async def test_failed_delivery_is_reported_without_the_provider_error(sf, monkeypatch):
    """Provider errors quote the webhook URL or bot token; the build log is
    readable by everyone who can see the build."""
    from app.models.notification import NotificationDelivery

    async def failing_slack(config, message, recipient):
        raise RuntimeError(f"404 for url '{config['webhook_url']}'")

    monkeypatch.setattr("app.services.notification_service._send_slack", failing_slack)
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 0
    assert len(reports) == 1
    assert "channel 'deploy-alerts' could not be delivered" in reports[0]
    assert "hook-secret" not in reports[0] and "hooks.slack" not in reports[0]
    async with sf() as db:
        row = (await db.execute(select(NotificationDelivery))).scalar_one()
    assert row.status == "failed" and "hook-secret" in row.error


async def test_a_failing_entry_does_not_stop_the_next(sf, providers, monkeypatch):
    async def failing_slack(config, message, recipient):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.services.notification_service._send_slack", failing_slack)
    yaml_content = _yaml("    - deploy-alerts\n    - tg-ops\n")
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        await seed_channel(db, "tg-ops", "telegram")
        ids = await seed_build(db, yaml_content=yaml_content)
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 1 and len(reports) == 1
    assert len(providers["telegram"]) == 1


async def test_telegram_values_are_html_escaped_but_authored_tags_are_kept(sf, providers):
    """Telegram reads the message as HTML and rejects a stray < or &."""
    yaml_content = _yaml(
        "    - tg-default\n"
        "    - channel: tg-custom\n"
        "      message: \"<b>${{ pipeline.name }}</b> failed at ${{ build.failed_step }}\"\n"
    )
    async with sf() as db:
        await seed_channel(db, "tg-default", "telegram")
        await seed_channel(db, "tg-custom", "telegram")
        ids = await seed_build(db, yaml_content=yaml_content, step_name="build <web> & api",
                               branch="feat/a&b")
        await db.commit()

    sent, _ = await _send(sf, ids["build"])

    assert sent == 2
    default, custom = (c["message"] for c in providers["telegram"])
    assert "step \"build &lt;web&gt; &amp; api\"" in default
    assert "Branch: feat/a&amp;b" in default
    assert custom == "<b>deploy-staging</b> failed at build &lt;web&gt; &amp; api"


async def test_slack_and_email_values_are_not_escaped(sf, providers):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, step_name="build <web> & api")
        await db.commit()

    await _send(sf, ids["build"])

    assert "step \"build <web> & api\"" in providers["slack"][0]["message"]


async def test_email_entry_without_recipient_uses_default_subject(sf, providers):
    async with sf() as db:
        await seed_channel(db, "ops-email", "email")
        ids = await seed_build(db, yaml_content=_yaml("    - ops-email\n"))
        await db.commit()

    await _send(sf, ids["build"])

    assert providers["email"][0]["subject"] == "Build #428 of deploy-staging failed"


async def test_pipeline_without_the_block_sends_nothing(sf, providers):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, yaml_content="name: x\nstages:\n  - name: a\n    steps:\n      - run: x\n")
        await db.commit()

    sent, reports = await _send(sf, ids["build"])

    assert sent == 0 and reports == [] and providers["slack"] == []


async def test_pipeline_with_no_yaml_sends_nothing(sf, providers):
    async with sf() as db:
        ids = await seed_build(db, yaml_content=None)
        await db.commit()

    assert await _send(sf, ids["build"]) == (0, [])


async def test_build_with_no_failed_step_still_sends(sf, providers):
    async with sf() as db:
        await seed_channel(db, "deploy-alerts")
        ids = await seed_build(db, step_status="success")
        await db.commit()

    sent, _ = await _send(sf, ids["build"])

    assert sent == 1
    assert "Failed at:" not in providers["slack"][0]["message"]


async def test_problems_go_to_the_server_log_when_there_is_no_reporter(sf, providers, caplog):
    import logging

    from app.services.build_notifications import send_failure_notifications

    async with sf() as db:
        ids = await seed_build(db, yaml_content=_yaml("    - missing-channel\n"))
        await db.commit()
    with caplog.at_level(logging.WARNING, logger="app.services.build_notifications"):
        async with sf() as db:
            build = await load_build(db, ids["build"])
            sent = await send_failure_notifications(
                db, build, secrets={}, env_vars={}, builtins=builtins_for(build),
            )

    assert sent == 0
    assert any("missing-channel" in r.getMessage() for r in caplog.records)


def test_default_yaml_fixture_is_valid():
    from app.services.pipeline_compiler import validate_pipeline

    assert validate_pipeline(PIPELINE_YAML) == []
