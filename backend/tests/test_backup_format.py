"""The backup file: readable header, passphrase-encrypted body."""
import json

import pytest

from app.services.backup import format as backup_format
from app.services.backup.format import (
    MAGIC,
    BackupFormatError,
    WrongPassphrase,
    read_backup,
    read_header,
    write_backup,
)

PASSPHRASE = "correct horse battery"
PAYLOAD = {"tables": {"users": [{"id": "1", "email": "ünïcode@example.com"}], "roles": []}}


@pytest.fixture(autouse=True)
def fast_key_derivation(monkeypatch):
    """The real cost is a tenth of a second per file; tests write many."""
    monkeypatch.setattr(backup_format, "SCRYPT_N", 2**10)


def _file(passphrase=PASSPHRASE, **overrides):
    fields = {"kind": "manual", "schema_revision": "023", "created_at": "2026-10-08T03:00:00+00:00",
              "counts": {"users": 1, "roles": 0}}
    return write_backup(PAYLOAD, passphrase, **{**fields, **overrides})


def test_round_trip():
    header, payload = read_backup(_file(), PASSPHRASE)

    assert payload == PAYLOAD
    assert (header.format, header.kind, header.schema_revision) == (1, "manual", "023")
    assert header.created_at == "2026-10-08T03:00:00+00:00"
    assert header.counts == {"users": 1, "roles": 0}


def test_the_header_is_readable_without_the_passphrase():
    header = read_header(_file(kind="scheduled"))

    assert header.kind == "scheduled" and header.counts["users"] == 1
    assert header.kdf["name"] == "scrypt" and header.kdf["n"] == 2**10


def test_the_body_does_not_contain_the_configuration_in_the_clear():
    data = _file()
    assert b"example.com" not in data and b"users\":[{" not in data


def test_two_backups_of_the_same_content_differ():
    assert _file() != _file(), "a fresh salt and nonce every time"


def test_a_wrong_passphrase_is_refused():
    with pytest.raises(WrongPassphrase) as exc:
        read_backup(_file(), "not the passphrase")
    assert str(exc.value) == "The passphrase is wrong or the file is damaged."


def test_a_changed_byte_in_the_body_is_refused():
    data = bytearray(_file())
    data[-5] ^= 0x01
    with pytest.raises(WrongPassphrase):
        read_backup(bytes(data), PASSPHRASE)


def test_a_changed_header_is_refused_even_though_it_still_parses():
    """Someone edits the header to claim another schema revision."""
    data = _file()
    assert b'"schema_revision":"023"' in data
    forged = data.replace(b'"schema_revision":"023"', b'"schema_revision":"999"')

    assert read_header(forged).schema_revision == "999"
    with pytest.raises(WrongPassphrase):
        read_backup(forged, PASSPHRASE)


@pytest.mark.parametrize("cut", [0, 5, len(MAGIC), len(MAGIC) + 2, len(MAGIC) + 10])
def test_a_file_cut_short_in_the_header_is_refused(cut):
    with pytest.raises(BackupFormatError):
        read_header(_file()[:cut])


def test_a_file_cut_short_in_the_body_is_refused():
    with pytest.raises(WrongPassphrase):
        read_backup(_file()[:-20], PASSPHRASE)


@pytest.mark.parametrize("data", [b"", b"PK\x03\x04 a zip file", b"{\"tables\": {}}"])
def test_other_files_are_not_backups(data):
    with pytest.raises(BackupFormatError) as exc:
        read_header(data)
    assert str(exc.value) == "This is not a MegooCI backup file."


def _with_header(change):
    """A backup whose header was rewritten by *change* (so it no longer authenticates)."""
    data = _file()
    start = len(MAGIC) + 4
    length = int.from_bytes(data[len(MAGIC):start], "big")
    header = json.loads(data[start:start + length])
    change(header)
    new = json.dumps(header).encode()
    return MAGIC + len(new).to_bytes(4, "big") + new + data[start + length:]


def test_a_newer_format_is_named_as_such():
    def newer(header):
        header["format"] = 2

    with pytest.raises(BackupFormatError) as exc:
        read_header(_with_header(newer))
    assert "format 2" in str(exc.value) and "different version of MegooCI" in str(exc.value)


@pytest.mark.parametrize(
    "field, value",
    [("n", 2**30), ("n", 1000), ("n", 0), ("r", 1000), ("p", 0), ("name", "pbkdf2")],
)
def test_a_header_asking_for_an_unreasonable_key_derivation_is_refused(field, value):
    """A crafted file must not make the server spend minutes or gigabytes."""
    def change(header):
        header["kdf"][field] = value

    with pytest.raises(BackupFormatError) as exc:
        read_header(_with_header(change))
    assert "header cannot be read" in str(exc.value)


@pytest.mark.parametrize("n, r, allowed", [
    (2**15, 8, True),     # 32 MiB: what this server writes
    (2**18, 8, True),     # 256 MiB: the most a file may ask for
    (2**19, 8, False),    # 512 MiB
    (2**20, 16, False),   # 2 GiB: enough to get a small server killed mid-restore
    (2**17, 16, True),    # 256 MiB
    (2**18, 16, False),
])
def test_the_memory_a_header_may_ask_for_is_bounded(n, r, allowed):
    def change(header):
        header["kdf"].update(n=n, r=r)

    data = _with_header(change)
    if allowed:
        assert read_header(data).kdf["n"] == n
    else:
        with pytest.raises(BackupFormatError):
            read_header(data)


@pytest.mark.parametrize("missing", ["kdf", "nonce", "counts", "created_at", "schema_revision"])
def test_a_header_missing_a_field_is_refused(missing):
    def change(header):
        del header[missing]

    with pytest.raises(BackupFormatError):
        read_header(_with_header(change))


def test_a_header_that_claims_to_be_huge_is_refused():
    data = MAGIC + (10**9).to_bytes(4, "big") + b"{}"
    with pytest.raises(BackupFormatError):
        read_header(data)


def test_the_real_cost_parameters_are_the_defaults(monkeypatch):
    monkeypatch.undo()
    assert (backup_format.SCRYPT_N, backup_format.SCRYPT_R, backup_format.SCRYPT_P) == (2**15, 8, 1)
