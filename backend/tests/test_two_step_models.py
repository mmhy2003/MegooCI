"""The four tables of two-step verification, and the migration that makes them."""
import importlib.util
import os
import pathlib
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.models.base import Base
from app.models.two_step import TwoStepTicket, UserPasskey, UserRecoveryCode, UserTwoStep
from app.models.user import User
from tests._rbac import build_inmemory_factory, seed_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


def _passkey(user_id, credential_id=b"cred-1", name="Laptop") -> UserPasskey:
    return UserPasskey(user_id=user_id, credential_id=credential_id, public_key=b"key", name=name)


async def test_a_new_row_starts_with_nothing_set_up(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        db.add(UserTwoStep(user_id=user_id))
        await db.commit()

    async with sf() as db:
        row = await db.get(UserTwoStep, user_id)
        assert row.totp_secret_encrypted is None and row.totp_confirmed_at is None
        assert row.failed_attempts == 0 and row.locked_until is None


async def test_a_passkey_keeps_its_bytes_and_starts_unused(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        db.add(UserPasskey(
            user_id=user_id, credential_id=b"\x00\x01\xff", public_key=b"\xa5\x01\x02",
            transports=["usb", "nfc"], name="YubiKey",
        ))
        await db.commit()

    async with sf() as db:
        passkey = (await db.execute(sa.select(UserPasskey))).scalar_one()
        assert passkey.credential_id == b"\x00\x01\xff" and passkey.public_key == b"\xa5\x01\x02"
        assert passkey.transports == ["usb", "nfc"]
        assert passkey.sign_count == 0 and passkey.last_used_at is None


async def test_one_credential_belongs_to_one_passkey(sf):
    async with sf() as db:
        first, second = await seed_user(db), await seed_user(db)
        db.add(_passkey(first))
        await db.commit()
        db.add(_passkey(second))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_one_token_belongs_to_one_ticket(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        for _ in range(2):
            db.add(TwoStepTicket(
                user_id=user_id, purpose="verify", token_hash="a" * 64,
                expires_at=NOW + timedelta(minutes=5),
            ))
        with pytest.raises(IntegrityError):
            await db.commit()


async def test_everything_goes_when_the_user_is_deleted(sf):
    async with sf() as db:
        user_id = await seed_user(db)
        other = await seed_user(db)
        db.add_all([
            UserTwoStep(user_id=user_id),
            _passkey(user_id),
            UserRecoveryCode(user_id=user_id, code_hash="h"),
            TwoStepTicket(user_id=user_id, purpose="setup", token_hash="b" * 64,
                          expires_at=NOW + timedelta(minutes=15)),
            _passkey(other, credential_id=b"cred-2"),
        ])
        await db.commit()
        await db.execute(sa.delete(User).where(User.id == user_id))
        await db.commit()

    async with sf() as db:
        for model in (UserTwoStep, UserRecoveryCode, TwoStepTicket):
            assert (await db.execute(sa.select(model))).scalars().all() == []
        assert [p.user_id for p in (await db.execute(sa.select(UserPasskey))).scalars()] == [other]


# ── the migration ───────────────────────────────────────────────────────

_TABLES = ("user_two_step", "user_passkeys", "user_recovery_codes", "two_step_tickets")


@pytest.fixture
def migration(monkeypatch):
    """The migration module, with Alembic's ``op`` replaced by a recorder.

    The real thing is run against PostgreSQL by hand; here it only has to be
    importable without Alembic installed, and say what it would create.
    """
    recorder = types.SimpleNamespace(created={}, dropped=[])

    def create_table(name, *columns, **_):
        recorder.created[name] = {
            column.name for column in columns if isinstance(column, sa.Column)
        }

    fake = types.ModuleType("alembic")
    fake.op = types.SimpleNamespace(create_table=create_table, drop_table=recorder.dropped.append)
    monkeypatch.setitem(sys.modules, "alembic", fake)

    path = pathlib.Path(__file__).parent.parent / "alembic" / "versions" / "024_two_step_verification.py"
    spec = importlib.util.spec_from_file_location("migration_024", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.recorder = recorder
    return module


def test_the_migration_follows_023(migration):
    assert (migration.revision, migration.down_revision) == ("024", "023")


def test_the_migration_creates_the_columns_the_models_have(migration):
    migration.upgrade()

    assert set(migration.recorder.created) == set(_TABLES)
    for name in _TABLES:
        assert migration.recorder.created[name] == set(Base.metadata.tables[name].columns.keys()), name


def test_the_downgrade_drops_the_four_tables(migration):
    migration.downgrade()
    assert sorted(migration.recorder.dropped) == sorted(_TABLES)
