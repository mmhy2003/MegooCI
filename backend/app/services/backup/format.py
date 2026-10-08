"""The backup file.

    MEGOOCI-BACKUP\\n | header length (4 bytes) | header (JSON) | body

The header is not secret: it lets a backup be listed, and an incompatible one
refused, without the passphrase. The body is the configuration as gzip'd JSON,
encrypted with AES-256-GCM under a key derived from the passphrase with
scrypt. The header is authenticated together with the body, so neither can be
changed without the file failing to open.
"""

from __future__ import annotations

import base64
import gzip
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"MEGOOCI-BACKUP\n"
FORMAT_VERSION = 1
KINDS = ("manual", "scheduled", "pre-restore", "uploaded")

# scrypt cost. About a tenth of a second and 32 MiB per attempt, which makes
# guessing a passphrase offline slow. Stored in each header, so files written
# with other values still open.
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1

# A header is a few hundred bytes. Larger means the file is not ours.
_MAX_HEADER_BYTES = 64 * 1024
# Bounds on the cost a header may ask for, so a crafted file cannot make the
# server spend minutes or gigabytes deriving a key.
_MAX_SCRYPT_N = 2**20
_MAX_SCRYPT_R = 16
_MAX_SCRYPT_P = 4


class BackupFormatError(Exception):
    """The file cannot be read as a backup. The message is for the user."""


class WrongPassphrase(BackupFormatError):
    """The passphrase does not open the file, or the file was altered."""


@dataclass(frozen=True)
class Header:
    format: int
    created_at: str  # ISO 8601, UTC
    kind: str
    schema_revision: str
    counts: dict[str, int]
    kdf: dict[str, Any]
    nonce: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "created_at": self.created_at,
            "kind": self.kind,
            "schema_revision": self.schema_revision,
            "counts": self.counts,
            "kdf": self.kdf,
            "nonce": self.nonce,
        }


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _derive_key(passphrase: str, kdf: dict[str, Any]) -> bytes:
    return Scrypt(
        salt=base64.b64decode(kdf["salt"]), length=32, n=kdf["n"], r=kdf["r"], p=kdf["p"]
    ).derive(passphrase.encode("utf-8"))


def write_backup(
    payload: dict[str, Any],
    passphrase: str,
    *,
    kind: str,
    schema_revision: str,
    created_at: str,
    counts: dict[str, int],
) -> bytes:
    """The bytes of a backup file holding *payload*."""
    kdf = {
        "name": "scrypt",
        "salt": _b64(os.urandom(16)),
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
    }
    nonce = os.urandom(12)
    header = Header(
        format=FORMAT_VERSION,
        created_at=created_at,
        kind=kind,
        schema_revision=schema_revision,
        counts=dict(counts),
        kdf=kdf,
        nonce=_b64(nonce),
    )
    header_bytes = json.dumps(header.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    plaintext = gzip.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    body = AESGCM(_derive_key(passphrase, kdf)).encrypt(nonce, plaintext, header_bytes)
    return MAGIC + len(header_bytes).to_bytes(4, "big") + header_bytes + body


def _split(data: bytes) -> tuple[bytes, bytes]:
    """The header bytes and the body bytes of a backup file."""
    if not data.startswith(MAGIC):
        raise BackupFormatError("This is not a MegooCI backup file.")
    start = len(MAGIC) + 4
    length = int.from_bytes(data[len(MAGIC):start], "big")
    if len(data) < start or not 0 < length <= _MAX_HEADER_BYTES or len(data) < start + length:
        raise BackupFormatError("The backup file is damaged: its header is incomplete.")
    return data[start:start + length], data[start + length:]


def _parse_header(header_bytes: bytes) -> Header:
    try:
        raw = json.loads(header_bytes)
        if raw["format"] != FORMAT_VERSION:
            raise BackupFormatError(
                f"This backup uses format {raw['format']!r}; this server reads format "
                f"{FORMAT_VERSION}. It was made by a different version of MegooCI."
            )
        kdf = raw["kdf"]
        counts = raw["counts"]
        header = Header(
            format=raw["format"],
            created_at=str(raw["created_at"]),
            kind=str(raw["kind"]),
            schema_revision=str(raw["schema_revision"]),
            counts={str(table): int(count) for table, count in counts.items()},
            kdf={"name": kdf["name"], "salt": str(kdf["salt"]),
                 "n": int(kdf["n"]), "r": int(kdf["r"]), "p": int(kdf["p"])},
            nonce=str(raw["nonce"]),
        )
        n, r, p = header.kdf["n"], header.kdf["r"], header.kdf["p"]
        if (
            header.kdf["name"] != "scrypt"
            or not 2 <= n <= _MAX_SCRYPT_N or n & (n - 1)
            or not 1 <= r <= _MAX_SCRYPT_R
            or not 1 <= p <= _MAX_SCRYPT_P
            or len(base64.b64decode(header.nonce)) != 12
            or not base64.b64decode(header.kdf["salt"])
        ):
            raise ValueError("unusable key-derivation parameters")
    except BackupFormatError:
        raise
    except Exception as exc:
        raise BackupFormatError("The backup file is damaged: its header cannot be read.") from exc
    return header


def read_header(data: bytes) -> Header:
    """The header of a backup file. Needs no passphrase."""
    return _parse_header(_split(data)[0])


def read_backup(data: bytes, passphrase: str) -> tuple[Header, dict[str, Any]]:
    """The header and the payload of a backup file."""
    header_bytes, body = _split(data)
    header = _parse_header(header_bytes)
    try:
        plaintext = AESGCM(_derive_key(passphrase, header.kdf)).decrypt(
            base64.b64decode(header.nonce), body, header_bytes
        )
    except InvalidTag as exc:
        raise WrongPassphrase("The passphrase is wrong or the file is damaged.") from exc
    try:
        payload = json.loads(gzip.decompress(plaintext))
    except Exception as exc:
        raise BackupFormatError("The backup file is damaged: its content cannot be read.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
        raise BackupFormatError("The backup file is damaged: it holds no configuration.")
    return header, payload
