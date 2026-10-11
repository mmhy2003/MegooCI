"""Passkeys: registering one and signing in with it, checked for real with a
software authenticator, on good answers and bad ones."""
import copy
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

os.environ.setdefault("MEGOOCI_REDIS_URL", "redis://localhost:6379/0")

from app.config import get_settings
from app.models.two_step import UserPasskey
from app.models.user import User
from app.services.two_step import passkeys
from tests._passkey import SoftwareAuthenticator, b64url
from tests._rbac import build_inmemory_factory, seed_user

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def sf():
    engine, factory = await build_inmemory_factory()
    yield factory
    await engine.dispose()


async def _user(db) -> User:
    return await db.get(User, await seed_user(db))


async def _register(db, user, device=None, name="Laptop") -> tuple[SoftwareAuthenticator, UserPasskey]:
    device = device or SoftwareAuthenticator()
    options, challenge = await passkeys.registration_options(db, user)
    passkey = await passkeys.register(db, user.id, device.create(options), challenge, name, NOW)
    return device, passkey


async def _sign_in(db, user, device) -> UserPasskey:
    options, challenge = await passkeys.authentication_options(db, user.id)
    return await passkeys.verify(db, user.id, device.get(options), challenge, NOW)


# ── where passkeys work ─────────────────────────────────────────────────

@pytest.mark.parametrize("public_url, origin, rp_id", [
    ("http://localhost:3000", "http://localhost:3000", "localhost"),
    ("https://ci.example.com", "https://ci.example.com", "ci.example.com"),
    ("https://CI.Example.com/", "https://ci.example.com", "ci.example.com"),
    ("https://ci.example.com:443/app", "https://ci.example.com", "ci.example.com"),
    ("https://ci.example.com:8443", "https://ci.example.com:8443", "ci.example.com"),
    ("http://ci.example.com:80", "http://ci.example.com", "ci.example.com"),
])
def test_the_origin_and_relying_party_come_from_the_public_url(monkeypatch, public_url, origin, rp_id):
    monkeypatch.setattr(get_settings(), "MEGOOCI_PUBLIC_URL", public_url)
    assert (passkeys.origin(), passkeys.rp_id()) == (origin, rp_id)


# ── registering ─────────────────────────────────────────────────────────

async def test_the_options_ask_for_a_second_step_passkey(sf):
    async with sf() as db:
        user = await _user(db)
        options, challenge = await passkeys.registration_options(db, user)

        assert options["rp"] == {"name": "MegooCI", "id": "localhost"}
        assert options["user"]["name"] == user.email
        assert options["user"]["id"] == b64url(user.id.bytes)
        assert options["challenge"] == b64url(challenge) and len(challenge) >= 32
        assert options["attestation"] == "none"
        assert options["authenticatorSelection"]["userVerification"] == "preferred"
        assert options["authenticatorSelection"]["residentKey"] == "discouraged"
        assert options["excludeCredentials"] == []


async def test_a_passkey_is_registered_with_its_public_key_and_name(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user, name="  Work   laptop ")

        assert passkey.user_id == user.id and passkey.name == "Work laptop"
        assert bytes(passkey.credential_id) == device.credential_id
        assert passkey.transports == ["internal"]
        assert passkey.sign_count == 0 and passkey.last_used_at is None
        assert [p.id for p in await passkeys.list_for(db, user.id)] == [passkey.id]


@pytest.mark.parametrize("name, stored", [("", "Passkey"), (None, "Passkey"), ("   ", "Passkey"), ("x" * 300, "x" * 100)])
async def test_a_missing_or_overlong_name_is_tidied(sf, name, stored):
    async with sf() as db:
        _, passkey = await _register(db, await _user(db), name=name)
        assert passkey.name == stored


async def test_an_answer_to_another_challenge_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        device = SoftwareAuthenticator()
        options, _ = await passkeys.registration_options(db, user)
        _, another_challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, device.create(options), another_challenge, "x", NOW)
        assert await passkeys.count(db, user.id) == 0


async def test_an_answer_from_another_site_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        phishing = SoftwareAuthenticator(origin="https://megooci.evil.example")
        options, challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, phishing.create(options), challenge, "x", NOW)


async def test_an_answer_for_another_relying_party_is_refused(sf):
    async with sf() as db:
        user = await _user(db)
        device = SoftwareAuthenticator(rp_id="other.example")
        options, challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, device.create(options), challenge, "x", NOW)


@pytest.mark.parametrize("answer", [
    {},
    {"id": "x", "rawId": "x", "type": "public-key"},
    {"id": "x", "rawId": "x", "type": "public-key", "response": {}},
    {"id": "x", "rawId": "x", "type": "public-key",
     "response": {"clientDataJSON": "!!!", "attestationObject": "!!!"}},
    {"id": 1, "rawId": None, "type": "public-key", "response": "nonsense"},
])
async def test_a_malformed_answer_is_refused_not_a_crash(sf, answer):
    async with sf() as db:
        user = await _user(db)
        _, challenge = await passkeys.registration_options(db, user)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.register(db, user.id, answer, challenge, "x", NOW)


async def test_the_same_passkey_cannot_be_registered_twice(sf):
    async with sf() as db:
        user, other = await _user(db), await _user(db)
        device, _ = await _register(db, user)

        options, challenge = await passkeys.registration_options(db, user)
        assert options["excludeCredentials"][0]["id"] == b64url(device.credential_id)
        with pytest.raises(passkeys.AlreadyRegistered):
            await passkeys.register(db, user.id, device.create(options), challenge, "again", NOW)

        # ...nor by another account.
        options, challenge = await passkeys.registration_options(db, other)
        with pytest.raises(passkeys.AlreadyRegistered):
            await passkeys.register(db, other.id, device.create(options), challenge, "theirs", NOW)


async def test_an_account_holds_ten_passkeys_at_most(sf):
    async with sf() as db:
        user = await _user(db)
        for _ in range(9):
            await _register(db, user)
        # The tenth is asked for while there is still room...
        options, challenge = await passkeys.registration_options(db, user)
        await _register(db, user)

        # ...so by the time its answer arrives the account is full.
        with pytest.raises(passkeys.TooManyPasskeys):
            await passkeys.register(db, user.id, SoftwareAuthenticator().create(options), challenge, "x", NOW)
        with pytest.raises(passkeys.TooManyPasskeys):
            await passkeys.registration_options(db, user)
        assert await passkeys.count(db, user.id) == 10


# ── signing in ──────────────────────────────────────────────────────────

async def test_the_sign_in_options_list_the_accounts_passkeys(sf):
    async with sf() as db:
        user = await _user(db)
        first, _ = await _register(db, user)
        second, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)

        assert options["rpId"] == "localhost" and options["userVerification"] == "preferred"
        assert options["challenge"] == b64url(challenge)
        assert {c["id"] for c in options["allowCredentials"]} == {
            b64url(first.credential_id), b64url(second.credential_id)
        }


async def test_a_registered_passkey_signs_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user)

        used = await _sign_in(db, user, device)

        assert used.id == passkey.id and used.sign_count == 1
        assert used.last_used_at.replace(tzinfo=timezone.utc) == NOW
        assert (await _sign_in(db, user, device)).sign_count == 2


async def test_an_authenticator_that_does_not_count_keeps_working(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user, SoftwareAuthenticator(counts=False))

        assert (await _sign_in(db, user, device)).sign_count == 0
        assert (await _sign_in(db, user, device)).sign_count == 0


async def test_a_counter_that_went_back_is_refused_and_marked(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user)
        for _ in range(3):
            await _sign_in(db, user, device)

        # A copy of the key, made when its counter was at 1, is used next.
        device.sign_count = 1
        with pytest.raises(passkeys.PasskeyError) as refused:
            await _sign_in(db, user, device)

        assert refused.value.counter_went_back is True
        assert (await db.get(UserPasskey, passkey.id)).sign_count == 3


async def test_an_answer_to_another_challenge_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, passkey = await _register(db, user)
        options, _ = await passkeys.authentication_options(db, user.id)
        _, another_challenge = await passkeys.authentication_options(db, user.id)

        with pytest.raises(passkeys.PasskeyError) as refused:
            await passkeys.verify(db, user.id, device.get(options), another_challenge, NOW)

        assert refused.value.counter_went_back is False
        assert (await db.get(UserPasskey, passkey.id)).last_used_at is None


async def test_an_answer_from_another_site_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)
        device.origin = "https://megooci.evil.example"

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, device.get(options), challenge, NOW)


async def test_a_tampered_signature_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)
        answer = device.get(options)
        answer["response"]["signature"] = b64url(b"\x30\x06\x02\x01\x01\x02\x01\x01")

        with pytest.raises(passkeys.PasskeyError) as refused:
            await passkeys.verify(db, user.id, answer, challenge, NOW)
        assert refused.value.counter_went_back is False


async def test_a_passkey_that_is_not_registered_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        await _register(db, user)
        stranger = SoftwareAuthenticator()
        options, challenge = await passkeys.authentication_options(db, user.id)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, stranger.get(options), challenge, NOW)


async def test_one_accounts_passkey_does_not_sign_in_another(sf):
    async with sf() as db:
        owner, other = await _user(db), await _user(db)
        device, _ = await _register(db, owner)
        await _register(db, other)
        options, challenge = await passkeys.authentication_options(db, other.id)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, other.id, device.get(options), challenge, NOW)


async def test_a_key_swapped_for_another_does_not_sign_in(sf):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        impostor = SoftwareAuthenticator()
        impostor.credential_id = device.credential_id  # claims to be the registered one
        options, challenge = await passkeys.authentication_options(db, user.id)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, impostor.get(options), challenge, NOW)


@pytest.mark.parametrize("damage", [
    lambda a: a.clear(),
    lambda a: a.pop("rawId"),
    lambda a: a.update(rawId=12345),
    lambda a: a.update(rawId="!!! not base64 !!!"),
    lambda a: a.pop("response"),
    lambda a: a.update(response="nonsense"),
    lambda a: a["response"].pop("signature"),
    lambda a: a["response"].update(authenticatorData="AAAA"),
    lambda a: a["response"].update(clientDataJSON=b64url(b"not json")),
])
async def test_a_damaged_answer_is_refused_not_a_crash(sf, damage):
    async with sf() as db:
        user = await _user(db)
        device, _ = await _register(db, user)
        options, challenge = await passkeys.authentication_options(db, user.id)
        answer = copy.deepcopy(device.get(options))
        damage(answer)

        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, user.id, answer, challenge, NOW)


# ── renaming and removing ───────────────────────────────────────────────

async def test_a_passkey_is_renamed_by_its_owner_only(sf):
    async with sf() as db:
        owner, other = await _user(db), await _user(db)
        _, passkey = await _register(db, owner)

        assert await passkeys.rename(db, other.id, passkey.id, "Mine now") is None
        assert (await passkeys.rename(db, owner.id, passkey.id, " Desk  key ")).name == "Desk key"
        assert await passkeys.rename(db, owner.id, uuid.uuid4(), "x") is None


async def test_a_passkey_is_removed_by_its_owner_only(sf):
    async with sf() as db:
        owner, other = await _user(db), await _user(db)
        device, passkey = await _register(db, owner)

        assert await passkeys.remove(db, other.id, passkey.id) is False
        assert await passkeys.remove(db, owner.id, passkey.id) is True
        assert await passkeys.remove(db, owner.id, passkey.id) is False
        assert await passkeys.count(db, owner.id) == 0

        options, challenge = await passkeys.authentication_options(db, owner.id)
        with pytest.raises(passkeys.PasskeyError):
            await passkeys.verify(db, owner.id, device.get(options), challenge, NOW)


async def test_passkeys_are_listed_oldest_first(sf):
    async with sf() as db:
        user = await _user(db)
        names = []
        for index in range(3):
            device = SoftwareAuthenticator()
            options, challenge = await passkeys.registration_options(db, user)
            await passkeys.register(
                db, user.id, device.create(options), challenge, f"key {index}",
                NOW + timedelta(minutes=index),
            )
            names.append(f"key {index}")

        assert [p.name for p in await passkeys.list_for(db, user.id)] == names
