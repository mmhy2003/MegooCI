"""When a scheduled backup is due, which old ones go, and the settings."""
import os
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.services.backup import settings
from app.services.backup.schedule import InvalidSchedule, Schedule, is_due, latest_slot, to_prune
from app.services.backup.settings import InvalidSettings, RemoteConfig
from tests._backup import OTHER_SECRET_KEY, SECRET_KEY, rows
from tests._rbac import build_inmemory_factory


def at(day, hour=0, minute=0):
    """October 2026; the 5th is a Monday."""
    return datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc)


DAILY = Schedule(frequency="daily", time="03:00")
WEEKLY = Schedule(frequency="weekly", time="03:00", weekday=2)  # Wednesday


# ── the slot ────────────────────────────────────────────────────────────

def test_off_is_never_due():
    assert latest_slot(Schedule(), at(8, 12)) is None
    assert is_due(Schedule(), at(8, 12), since=None) is False


def test_daily_slot_is_today_once_the_time_has_passed_else_yesterday():
    assert latest_slot(DAILY, at(8, 3, 0)) == at(8, 3)
    assert latest_slot(DAILY, at(8, 23, 59)) == at(8, 3)
    assert latest_slot(DAILY, at(8, 2, 59)) == at(7, 3)


def test_weekly_slot_is_the_latest_such_weekday():
    assert latest_slot(WEEKLY, at(7, 3, 0)) == at(7, 3)      # Wednesday the 7th
    assert latest_slot(WEEKLY, at(9, 12)) == at(7, 3)
    assert latest_slot(WEEKLY, at(7, 2, 59)) == at(7, 3) - timedelta(days=7)
    assert latest_slot(WEEKLY, at(13, 23)) == at(7, 3)       # Tuesday: still last week's
    assert latest_slot(WEEKLY, at(14, 3)) == at(14, 3)


def test_the_slot_is_in_utc_whatever_zone_now_is_in():
    plus_two = timezone(timedelta(hours=2))
    now = datetime(2026, 10, 8, 4, 30, tzinfo=plus_two)  # 02:30 UTC
    assert latest_slot(DAILY, now) == at(7, 3)


# ── due ─────────────────────────────────────────────────────────────────

def test_due_when_a_slot_has_passed_since_the_last_run():
    assert is_due(DAILY, at(8, 3, 4), since=at(7, 3, 2)) is True
    assert is_due(DAILY, at(8, 3, 9), since=at(8, 3, 4)) is False
    assert is_due(DAILY, at(8, 2, 59), since=at(7, 3, 2)) is False


def test_turning_the_schedule_on_does_not_make_a_backup_at_once():
    saved = at(8, 10)
    assert is_due(DAILY, at(8, 10, 5), since=saved) is False
    assert is_due(DAILY, at(9, 3, 0), since=saved) is True


def test_a_server_that_was_down_for_days_makes_one_backup_not_several():
    assert is_due(DAILY, at(12, 9), since=at(7, 3, 1)) is True
    assert is_due(DAILY, at(12, 9, 5), since=at(12, 9)) is False


def test_never_run_and_never_saved_is_due():
    assert is_due(DAILY, at(8, 12), since=None) is True


@pytest.mark.parametrize("schedule, message", [
    (Schedule(frequency="hourly"), "Frequency must be one of"),
    (Schedule(frequency="daily", time="3:00"), "Time must be HH:MM"),
    (Schedule(frequency="daily", time="25:00"), "Time must be HH:MM"),
    (Schedule(frequency="daily", time="03:60"), "Time must be HH:MM"),
    (Schedule(frequency="daily", time="aa:bb"), "Time must be HH:MM"),
    (Schedule(frequency="weekly", weekday=7), "Weekday must be"),
    (Schedule(frequency="daily", keep=0), "Keep must be between 1 and 365"),
    (Schedule(frequency="daily", keep=1000), "Keep must be between 1 and 365"),
])
def test_invalid_schedules_say_what_is_wrong(schedule, message):
    with pytest.raises(InvalidSchedule) as exc:
        schedule.validate()
    assert message in str(exc.value)


def test_prune_keeps_the_newest():
    names = ["d5", "d4", "d3", "d2", "d1"]
    assert to_prune(names, 3) == ["d2", "d1"]
    assert to_prune(names, 5) == [] and to_prune(names, 9) == []
    assert to_prune(names, 0) == ["d4", "d3", "d2", "d1"], "never fewer than one"


# ── settings ────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _stored(sf):
    from app.models.system_setting import SystemSetting

    async with sf() as db:
        return {row["key"]: row["value"] for row in await rows(db, SystemSetting, SystemSetting.key)}


async def test_the_passphrase_is_stored_encrypted_and_read_back(sf):
    async with sf() as db:
        assert await settings.get_passphrase(db, SECRET_KEY) is None
        await settings.set_passphrase(db, SECRET_KEY, "correct horse battery")
        await db.commit()

    stored = await _stored(sf)
    assert "correct horse" not in stored["backup_passphrase"]
    async with sf() as db:
        assert await settings.get_passphrase(db, SECRET_KEY) == "correct horse battery"
        assert await settings.get_passphrase(db, OTHER_SECRET_KEY) is None, "unreadable = not set"


async def test_a_short_passphrase_is_refused(sf):
    async with sf() as db:
        with pytest.raises(InvalidSettings) as exc:
            await settings.set_passphrase(db, SECRET_KEY, "too short")
    assert "at least 12 characters" in str(exc.value)


async def test_schedule_defaults_round_trip_and_when_it_was_saved(sf):
    async with sf() as db:
        assert await settings.get_schedule(db) == Schedule()
        await settings.set_schedule(db, WEEKLY, at(8, 10))
        await db.commit()
    async with sf() as db:
        assert await settings.get_schedule(db) == WEEKLY
        assert (await settings.get_state(db))["schedule_saved_at"] == at(8, 10).isoformat()
        with pytest.raises(InvalidSchedule):
            await settings.set_schedule(db, Schedule(frequency="hourly"), at(8, 10))


async def test_unreadable_stored_settings_fall_back_to_defaults(sf):
    from app.models.system_setting import SystemSetting

    async with sf() as db:
        db.add_all([SystemSetting(key="backup_schedule", value="{not json"),
                    SystemSetting(key="backup_state", value="[1, 2]"),
                    SystemSetting(key="backup_remote", value='{"enabled": true, "bucket": "b"}')])
        await db.commit()
    async with sf() as db:
        assert await settings.get_schedule(db) == Schedule()
        assert await settings.get_state(db) == {"remote": {}}
        remote = await settings.get_remote(db, SECRET_KEY)
    assert remote.enabled is True and remote.bucket == "b" and remote.secret_access_key == ""


async def test_the_remote_secret_is_stored_encrypted(sf):
    config = RemoteConfig(enabled=True, endpoint_url="https://s3.example", region="eu", bucket="b",
                          prefix="ci/", access_key_id="AKIA", secret_access_key="very-secret")
    async with sf() as db:
        await settings.set_remote(db, SECRET_KEY, config)
        await db.commit()

    assert "very-secret" not in (await _stored(sf))["backup_remote"]
    async with sf() as db:
        assert await settings.get_remote(db, SECRET_KEY) == config


@pytest.mark.parametrize("config, message", [
    (RemoteConfig(enabled=True, access_key_id="a", secret_access_key="s"), "needs a bucket"),
    (RemoteConfig(enabled=True, bucket="b", secret_access_key="s"), "needs an access key id"),
    (RemoteConfig(enabled=True, bucket="b", access_key_id="a"), "needs a secret access key"),
    (RemoteConfig(endpoint_url="s3.example"), "must start with http"),
])
def test_remote_settings_that_cannot_work_are_refused(config, message):
    with pytest.raises(InvalidSettings) as exc:
        config.validate()
    assert message in str(exc.value)


def test_turned_off_remote_settings_may_be_incomplete_and_keys_get_the_prefix():
    RemoteConfig(enabled=False, bucket="").validate()
    assert RemoteConfig(prefix="/ci/backups/").object_key("x.mcbak") == "ci/backups/x.mcbak"
    assert RemoteConfig().object_key("x.mcbak") == "x.mcbak"
