"""Failure notifications declared in a pipeline's YAML.

A pipeline can name the notification channels that are told when one of its
builds fails::

    notifications:
      on_failure:
        - deploy-alerts
        - channel: ops-email
          recipient: oncall@example.com
          subject: "Deploy failed"
          message: "Build #${{ build.number }} failed: ${{ build.url }}"

The server sends these itself, after the build has ended as failed and its
agent has been released. The YAML rules live in ``pipeline_compiler``; this
module reads the block and sends the messages.

Everything here works on plain values, never on ORM objects held by the
build executor: sending can roll a session back, and a rollback expires every
object loaded in it.
"""

from __future__ import annotations

import asyncio
import html
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.build import Build, Step
from app.models.notification import NotificationChannel
from app.models.pipeline import Pipeline
from app.services.notification_service import send_notification
from app.services.step_actions.interpolation import interpolate_value

logger = logging.getLogger(__name__)

Report = Callable[[str], Awaitable[None]]
Values = dict[str, dict[str, str]]

# How long one entry may take. A stalled provider must not hold a build
# worker; the entry is abandoned and the next one is tried.
SEND_TIMEOUT_SECONDS: float = 30

# Channel types whose message format gives <, > and & a meaning: Telegram
# parses the text as HTML, Slack reads <...> as links and mentions. Values
# substituted into a message are escaped for these; text the author wrote is
# left alone. Email is escaped by its sender, for the HTML part only.
_ESCAPED_CHANNEL_TYPES = {"telegram", "slack"}


@dataclass(frozen=True)
class FailureNotification:
    """One ``on_failure`` entry, in normalized form."""

    channel: str
    message: str | None = None
    subject: str | None = None
    recipient: str | None = None


@dataclass(frozen=True)
class FailedBuild:
    """Everything needed to notify about a failed build, as plain values."""

    build_id: uuid.UUID
    pipeline_id: uuid.UUID
    number: int
    failed_stage: str
    failed_step: str
    failed_step_id: uuid.UUID | None
    secrets: dict[str, str]
    env_vars: dict[str, str]
    builtins: Values

    @classmethod
    def capture(
        cls,
        build: Build,
        secrets: dict[str, str],
        env_vars: dict[str, str],
        builtins: Values,
    ) -> FailedBuild:
        """Snapshot *build* while its session is still usable. Expects
        ``build.stages`` and their steps to be loaded."""
        stage_name, step_name, step = failed_step_of(build)
        return cls(
            build_id=build.id,
            pipeline_id=build.pipeline_id,
            number=build.number,
            failed_stage=stage_name,
            failed_step=step_name,
            failed_step_id=step.id if step is not None else None,
            secrets=dict(secrets),
            env_vars=dict(env_vars),
            builtins={namespace: dict(values) for namespace, values in builtins.items()},
        )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def failure_notifications(yaml_content: str | None) -> list[FailureNotification]:
    """Read the ``notifications.on_failure`` entries from pipeline YAML.

    Never raises. Missing, unparseable or malformed input yields an empty
    list, and malformed entries are skipped: the validator is what reports
    mistakes to the author, so this reader only has to be safe.
    """
    try:
        data = yaml.safe_load(yaml_content or "")
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict):
        return []
    block = data.get("notifications")
    if not isinstance(block, dict):
        return []
    entries = block.get("on_failure")
    if not isinstance(entries, list):
        return []

    result: list[FailureNotification] = []
    for entry in entries:
        if isinstance(entry, str):
            if entry.strip():
                result.append(FailureNotification(channel=entry.strip()))
        elif isinstance(entry, dict):
            channel = _text(entry.get("channel"))
            if channel:
                result.append(
                    FailureNotification(
                        channel=channel.strip(),
                        message=_text(entry.get("message")),
                        subject=_text(entry.get("subject")),
                        recipient=_text(entry.get("recipient")),
                    )
                )
    return result


def failed_step_of(build: Build) -> tuple[str, str, Step | None]:
    """The first failed step of *build* as ``(stage name, step name, step)``,
    or ``("", "", None)``. Expects ``build.stages`` and their steps loaded."""
    for stage in sorted(build.stages, key=lambda s: s.sort_order):
        for step in sorted(stage.steps, key=lambda s: s.sort_order):
            if step.status == "failed":
                return stage.name, step.name, step
    return "", "", None


def build_url(build_id: Any) -> str:
    base = get_settings().MEGOOCI_PUBLIC_URL.rstrip("/")
    return f"{base}/builds/{build_id}"


def default_subject(values: Values) -> str:
    build = values.get("build", {})
    pipeline = values.get("pipeline", {})
    return f"Build #{build.get('number', '')} of {pipeline.get('name', '')} failed"


def default_message(values: Values) -> str:
    """The message used when an entry does not write its own."""
    build = values.get("build", {})
    lines = [default_subject(values)]

    details = []
    project_name = values.get("project", {}).get("name")
    if project_name:
        details.append(f"Project: {project_name}")
    if build.get("branch"):
        details.append(f"Branch: {build['branch']}")
    if build.get("commit"):
        details.append(f"Commit: {build['commit'][:7]}")
    if details:
        lines.append(" | ".join(details))

    if build.get("failed_step"):
        lines.append(
            f"Failed at: stage \"{build.get('failed_stage', '')}\", "
            f"step \"{build['failed_step']}\""
        )
    if build.get("url"):
        lines.append(build["url"])
    return "\n".join(lines)


def _escaped(values: Values) -> Values:
    return {
        namespace: {key: html.escape(value, quote=False) for key, value in entries.items()}
        for namespace, entries in values.items()
    }


async def send_failure_notifications(
    db: AsyncSession,
    failed: FailedBuild,
    *,
    report: Report | None = None,
) -> int:
    """Send every ``on_failure`` notification of the failed build's pipeline.

    Reads the block from the pipeline's YAML as stored now. Entries are
    independent: a problem with one is passed to *report* (one short line,
    safe to show in the build log) and the rest still go out. Returns the
    number of messages sent.
    """

    async def _report(text: str) -> None:
        try:
            if report is not None:
                await report(text)
            else:
                logger.warning("build %s: %s", failed.build_id, text)
        except Exception:
            logger.exception("build %s: could not report: %s", failed.build_id, text)

    pipeline = (
        await db.execute(
            select(Pipeline.name, Pipeline.yaml_content).where(Pipeline.id == failed.pipeline_id)
        )
    ).first()
    if pipeline is None:
        return 0
    pipeline_name, yaml_content = pipeline
    entries = failure_notifications(yaml_content)
    if not entries:
        return 0

    values: Values = {namespace: dict(entries_) for namespace, entries_ in failed.builtins.items()}
    values.setdefault("pipeline", {}).setdefault("name", pipeline_name)
    values["build"] = {
        **values.get("build", {}),
        "number": str(failed.number),
        "status": "failed",
        "failed_stage": failed.failed_stage,
        "failed_step": failed.failed_step,
        "url": build_url(failed.build_id),
    }
    escaped = _escaped(values)

    sent = 0
    for entry in entries:
        channel = (
            await db.execute(
                select(
                    NotificationChannel.id,
                    NotificationChannel.enabled,
                    NotificationChannel.channel_type,
                ).where(NotificationChannel.name == entry.channel)
            )
        ).first()
        if channel is None:
            await _report(
                f"Failure notification not sent: channel '{entry.channel}' was not found."
            )
            continue
        channel_id, enabled, channel_type = channel
        if not enabled:
            await _report(
                f"Failure notification not sent: channel '{entry.channel}' is disabled."
            )
            continue

        shown = escaped if channel_type in _ESCAPED_CHANNEL_TYPES else values
        message = (
            interpolate_value(entry.message, failed.secrets, failed.env_vars, shown)
            if entry.message
            else default_message(shown)
        )
        subject = (
            interpolate_value(entry.subject, failed.secrets, failed.env_vars, values)
            if entry.subject
            else default_subject(values)
        )

        try:
            delivery = await asyncio.wait_for(
                send_notification(
                    db,
                    channel_id,
                    message,
                    subject=subject,
                    recipient=entry.recipient,
                    build_id=failed.build_id,
                ),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _rollback(db)
            await _report(
                f"Failure notification via channel '{entry.channel}' timed out and "
                "was not sent."
            )
            continue
        except Exception:
            logger.exception(
                "build %s: failure notification via %s", failed.build_id, entry.channel
            )
            await _rollback(db)
            delivered = False
        else:
            delivered = delivery.status == "sent"

        if delivered:
            sent += 1
        else:
            # The provider's error text is deliberately left out: it can quote
            # the channel's webhook URL or bot token, and the build log is
            # readable by everyone who can see the build.
            await _report(
                f"Failure notification via channel '{entry.channel}' could not be "
                "delivered. An administrator can find the error in the notification "
                "delivery history."
            )
    return sent


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        logger.exception("could not roll back after a failed notification")
