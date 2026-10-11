"""The time, read in one place so that tests can set it."""

from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)
