"""When a scheduled backup is due, and which old ones to delete.

Pure functions: the scheduled task supplies the clock and the state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

FREQUENCIES = ("off", "daily", "weekly")
DEFAULT_KEEP = 14


class InvalidSchedule(ValueError):
    """The message is for the user."""


@dataclass(frozen=True)
class Schedule:
    frequency: str = "off"
    time: str = "03:00"  # HH:MM, UTC
    weekday: int = 0  # 0 = Monday; used by "weekly"
    keep: int = DEFAULT_KEEP

    def validate(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise InvalidSchedule(f"Frequency must be one of: {', '.join(FREQUENCIES)}.")
        try:
            hour, minute = _clock(self.time)
        except ValueError as exc:
            raise InvalidSchedule("Time must be HH:MM, for example 03:00.") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise InvalidSchedule("Time must be HH:MM, for example 03:00.")
        if not 0 <= self.weekday <= 6:
            raise InvalidSchedule("Weekday must be 0 (Monday) to 6 (Sunday).")
        if not 1 <= self.keep <= 365:
            raise InvalidSchedule("Keep must be between 1 and 365 backups.")


def _clock(text: str) -> tuple[int, int]:
    hour, _, minute = text.partition(":")
    if len(hour) != 2 or len(minute) != 2:
        raise ValueError(text)
    return int(hour), int(minute)


def latest_slot(schedule: Schedule, now: datetime) -> datetime | None:
    """The most recent moment, not after *now*, at which the schedule calls
    for a backup. None when the schedule is off."""
    if schedule.frequency == "off":
        return None
    now = now.astimezone(timezone.utc)
    hour, minute = _clock(schedule.time)
    slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if schedule.frequency == "weekly":
        slot -= timedelta(days=(slot.weekday() - schedule.weekday) % 7)
        step = timedelta(days=7)
    else:
        step = timedelta(days=1)
    if slot > now:
        slot -= step
    return slot


def is_due(schedule: Schedule, now: datetime, *, since: datetime | None) -> bool:
    """Whether a scheduled backup should be made now.

    *since* is the later of the last scheduled run and the moment the schedule
    was last saved. A backup is due when a slot has passed after it: so
    turning the schedule on does not make a backup at once, and a server that
    was down over several slots makes one backup when it returns, not several.
    """
    slot = latest_slot(schedule, now)
    if slot is None:
        return False
    return since is None or slot > since.astimezone(timezone.utc)


def to_prune(scheduled_names_newest_first: list[str], keep: int) -> list[str]:
    """The scheduled backups beyond the newest *keep*."""
    return scheduled_names_newest_first[max(keep, 1):]
