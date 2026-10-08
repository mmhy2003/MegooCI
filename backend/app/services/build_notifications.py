"""Build notifications declared in a pipeline's YAML.

A pipeline can name the notification channels that are told about its builds::

    notifications:
      on_start:
        - team-chat
      on_failure:
        - channel: ops-email
          recipient: oncall@example.com
          subject: "Deploy failed"
          message: "Build #${{ build.number }} failed: ${{ build.url }}"
      on_complete:
        - team-chat

The server sends these itself: ``on_start`` and ``on_waiting`` while the build
runs, the others after it has ended and its agent has been released. The YAML
rules live in ``pipeline_compiler``; this module reads the block, decides which
entries a moment of a build calls for, and sends the messages.

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
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.build import Build, Stage, Step
from app.models.notification import NotificationChannel
from app.models.pipeline import Pipeline
from app.services.notification_service import send_notification
from app.services.pipeline_compiler import NOTIFICATION_EVENTS
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

# The event that describes how a build ended; ``on_complete`` borrows its words.
_OUTCOME_EVENT = {"success": "on_success", "failed": "on_failure", "cancelled": "on_cancelled"}

# event -> (how the subject ends, what the build log calls the notification)
_WORDING = {
    "on_start": ("started", "Start notification"),
    "on_waiting": ("is waiting for approval", "Approval notification"),
    "on_success": ("succeeded", "Success notification"),
    "on_fixed": ("is fixed", "Fixed notification"),
    "on_failure": ("failed", "Failure notification"),
    "on_cancelled": ("was cancelled", "Cancellation notification"),
    "on_complete": ("", "Completion notification"),
}


@dataclass(frozen=True)
class Notification:
    """One entry of an event, in normalized form."""

    channel: str
    message: str | None = None
    subject: str | None = None
    recipient: str | None = None


@dataclass(frozen=True)
class BuildSnapshot:
    """A build at the moment something worth telling happened, as plain values.

    ``status`` is ``running`` for a build that has just started or is waiting
    for approval (``waiting_step`` says which), otherwise how it ended.
    """

    build_id: uuid.UUID
    pipeline_id: uuid.UUID
    number: int
    failed_stage: str
    failed_step: str
    failed_step_id: uuid.UUID | None
    secrets: dict[str, str]
    env_vars: dict[str, str]
    builtins: Values
    status: str = "failed"
    branch: str | None = None
    waiting_stage: str = ""
    waiting_step: str = ""
    # The step under which problems with a notification are logged.
    log_step_id: uuid.UUID | None = None

    @classmethod
    def capture(
        cls,
        build: Build,
        secrets: dict[str, str],
        env_vars: dict[str, str],
        builtins: Values,
        *,
        status: str | None = None,
        waiting: tuple[Stage, Step] | None = None,
    ) -> BuildSnapshot:
        """Snapshot *build* while its session is still usable. Expects
        ``build.stages`` and their steps to be loaded.

        *status* defaults to the build's own. Pass *waiting* (stage, step)
        when the build is about to wait for approval at that step.
        """
        status = status or build.status
        stage_name, step_name, failed_step = ("", "", None)
        if status == "failed":
            stage_name, step_name, failed_step = failed_step_of(build)

        steps = [
            step
            for stage in sorted(build.stages, key=lambda s: s.sort_order)
            for step in sorted(stage.steps, key=lambda s: s.sort_order)
        ]
        if waiting is not None:
            log_step = waiting[1]
        elif failed_step is not None:
            log_step = failed_step
        elif status == "running":
            log_step = steps[0] if steps else None
        else:
            ran = [step for step in steps if step.started_at is not None]
            log_step = ran[-1] if ran else None

        return cls(
            build_id=build.id,
            pipeline_id=build.pipeline_id,
            number=build.number,
            failed_stage=stage_name,
            failed_step=step_name,
            failed_step_id=failed_step.id if failed_step is not None else None,
            secrets=dict(secrets),
            env_vars=dict(env_vars),
            builtins={namespace: dict(values) for namespace, values in builtins.items()},
            status=status,
            branch=build.branch or None,
            waiting_stage=waiting[0].name if waiting is not None else "",
            waiting_step=waiting[1].name if waiting is not None else "",
            log_step_id=log_step.id if log_step is not None else None,
        )


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _entries(value: Any) -> list[Notification]:
    if not isinstance(value, list):
        return []
    result: list[Notification] = []
    for entry in value:
        if isinstance(entry, str):
            if entry.strip():
                result.append(Notification(channel=entry.strip()))
        elif isinstance(entry, dict):
            channel = _text(entry.get("channel"))
            if channel:
                result.append(
                    Notification(
                        channel=channel.strip(),
                        message=_text(entry.get("message")),
                        subject=_text(entry.get("subject")),
                        recipient=_text(entry.get("recipient")),
                    )
                )
    return result


def notifications_by_event(yaml_content: str | None) -> dict[str, list[Notification]]:
    """Read the ``notifications`` block from pipeline YAML: the entries of
    each event that has any.

    Never raises. Missing, unparseable or malformed input yields an empty
    dict, and malformed entries are skipped: the validator is what reports
    mistakes to the author, so this reader only has to be safe.
    """
    try:
        data = yaml.safe_load(yaml_content or "")
    except yaml.YAMLError:
        return {}
    if not isinstance(data, dict):
        return {}
    block = data.get("notifications")
    if not isinstance(block, dict):
        return {}
    found = {event: _entries(block.get(event)) for event in NOTIFICATION_EVENTS}
    return {event: entries for event, entries in found.items() if entries}


def matching_events(status: str, *, waiting: bool = False, fixed: bool = False) -> tuple[str, ...]:
    """The events a moment of a build matches, most specific first."""
    if status == "running":
        return ("on_waiting",) if waiting else ("on_start",)
    if status == "success":
        return (("on_fixed",) if fixed else ()) + ("on_success", "on_complete")
    if status == "failed":
        return ("on_failure", "on_complete")
    if status == "cancelled":
        return ("on_cancelled", "on_complete")
    return ()


def select_notifications(
    by_event: dict[str, list[Notification]], events: tuple[str, ...]
) -> list[tuple[str, Notification]]:
    """The entries to send for *events* (most specific first), each with the
    event it comes from.

    A destination — a channel and a recipient — gets the entries of the first
    event that lists it and is skipped in the later, more general ones.
    Entries within one event are never dropped.
    """
    chosen: list[tuple[str, Notification]] = []
    taken: set[tuple[str, str | None]] = set()
    for event in events:
        entries = [
            entry for entry in by_event.get(event, []) if (entry.channel, entry.recipient) not in taken
        ]
        chosen.extend((event, entry) for entry in entries)
        taken.update((entry.channel, entry.recipient) for entry in entries)
    return chosen


def failed_step_of(build: Build) -> tuple[str, str, Step | None]:
    """The first failed step of *build* as ``(stage name, step name, step)``,
    or ``("", "", None)``. Expects ``build.stages`` and their steps loaded."""
    for stage in sorted(build.stages, key=lambda s: s.sort_order):
        for step in sorted(stage.steps, key=lambda s: s.sort_order):
            if step.status == "failed":
                return stage.name, step.name, step
    return "", "", None


async def previous_build_failed(db: AsyncSession, snapshot: BuildSnapshot) -> bool:
    """Whether the build before *snapshot* — same pipeline, same branch, and
    ended as success or failed — was a failure. Cancelled and unfinished
    builds are skipped."""
    same_branch = (
        Build.branch == snapshot.branch
        if snapshot.branch
        else or_(Build.branch.is_(None), Build.branch == "")
    )
    previous = await db.scalar(
        select(Build.status)
        .where(
            Build.pipeline_id == snapshot.pipeline_id,
            Build.number < snapshot.number,
            Build.status.in_(("success", "failed")),
            same_branch,
        )
        .order_by(Build.number.desc())
        .limit(1)
    )
    return previous == "failed"


def build_url(build_id: Any) -> str:
    base = get_settings().MEGOOCI_PUBLIC_URL.rstrip("/")
    return f"{base}/builds/{build_id}"


def _outcome_event(event: str, values: Values) -> str:
    """*event*, or for ``on_complete`` the event that describes the outcome."""
    if event != "on_complete":
        return event
    return _OUTCOME_EVENT.get(values.get("build", {}).get("status", ""), "on_success")


def default_subject(values: Values, event: str) -> str:
    build = values.get("build", {})
    pipeline = values.get("pipeline", {})
    ending = _WORDING[_outcome_event(event, values)][0]
    return f"Build #{build.get('number', '')} of {pipeline.get('name', '')} {ending}"


def default_message(values: Values, event: str) -> str:
    """The message used when an entry does not write its own."""
    build = values.get("build", {})
    lines = [default_subject(values, event)]

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
    if build.get("waiting_step"):
        lines.append(
            f"Waiting at: stage \"{build.get('waiting_stage', '')}\", "
            f"step \"{build['waiting_step']}\""
        )
    if build.get("url"):
        lines.append(build["url"])
    return "\n".join(lines)


def _escaped(values: Values) -> Values:
    return {
        namespace: {key: html.escape(value, quote=False) for key, value in entries.items()}
        for namespace, entries in values.items()
    }


async def send_build_notifications(
    db: AsyncSession,
    snapshot: BuildSnapshot,
    *,
    report: Report | None = None,
) -> int:
    """Send the notifications the moment in *snapshot* calls for.

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
                logger.warning("build %s: %s", snapshot.build_id, text)
        except Exception:
            logger.exception("build %s: could not report: %s", snapshot.build_id, text)

    pipeline = (
        await db.execute(
            select(Pipeline.name, Pipeline.yaml_content).where(Pipeline.id == snapshot.pipeline_id)
        )
    ).first()
    if pipeline is None:
        return 0
    pipeline_name, yaml_content = pipeline
    by_event = notifications_by_event(yaml_content)
    if not by_event:
        return 0

    fixed = (
        snapshot.status == "success"
        and "on_fixed" in by_event
        and await previous_build_failed(db, snapshot)
    )
    events = matching_events(snapshot.status, waiting=bool(snapshot.waiting_step), fixed=fixed)
    chosen = select_notifications(by_event, events)
    if not chosen:
        return 0

    values: Values = {namespace: dict(entries_) for namespace, entries_ in snapshot.builtins.items()}
    values.setdefault("pipeline", {}).setdefault("name", pipeline_name)
    values["build"] = {
        **values.get("build", {}),
        "number": str(snapshot.number),
        "status": snapshot.status,
        "failed_stage": snapshot.failed_stage,
        "failed_step": snapshot.failed_step,
        "waiting_stage": snapshot.waiting_stage,
        "waiting_step": snapshot.waiting_step,
        "url": build_url(snapshot.build_id),
    }
    escaped = _escaped(values)

    sent = 0
    for event, entry in chosen:
        label = _WORDING[event][1]
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
            await _report(f"{label} not sent: channel '{entry.channel}' was not found.")
            continue
        channel_id, enabled, channel_type = channel
        if not enabled:
            await _report(f"{label} not sent: channel '{entry.channel}' is disabled.")
            continue

        shown = escaped if channel_type in _ESCAPED_CHANNEL_TYPES else values
        message = (
            interpolate_value(entry.message, snapshot.secrets, snapshot.env_vars, shown)
            if entry.message
            else default_message(shown, event)
        )
        subject = (
            interpolate_value(entry.subject, snapshot.secrets, snapshot.env_vars, values)
            if entry.subject
            else default_subject(values, event)
        )

        try:
            delivery = await asyncio.wait_for(
                send_notification(
                    db,
                    channel_id,
                    message,
                    subject=subject,
                    recipient=entry.recipient,
                    build_id=snapshot.build_id,
                ),
                timeout=SEND_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await _rollback(db)
            await _report(
                f"{label} via channel '{entry.channel}' timed out and was not sent."
            )
            continue
        except Exception:
            logger.exception(
                "build %s: %s via %s", snapshot.build_id, label.lower(), entry.channel
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
                f"{label} via channel '{entry.channel}' could not be "
                "delivered. An administrator can find the error in the notification "
                "delivery history."
            )
    return sent


async def _rollback(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        logger.exception("could not roll back after a failed notification")
