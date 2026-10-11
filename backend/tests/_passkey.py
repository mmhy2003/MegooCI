"""A passkey authenticator in software, for tests.

It does what a browser and a security key do together: makes a real P-256 key
pair, and answers the server's options with real, signed WebAuthn responses.
The server's checks therefore run for real, on good answers and bad ones.
"""
import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

ORIGIN = "http://localhost:3000"  # the default MEGOOCI_PUBLIC_URL
RP_ID = "localhost"

_USER_PRESENT = 0x01
_USER_VERIFIED = 0x04
_HAS_CREDENTIAL = 0x40


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class SoftwareAuthenticator:
    def __init__(self, *, origin: str = ORIGIN, rp_id: str = RP_ID, counts: bool = True) -> None:
        self.origin = origin
        self.rp_id = rp_id
        # Some authenticators (most phones and laptops) always report 0.
        self.counts = counts
        self.sign_count = 0
        self.credential_id = os.urandom(32)
        self._key = ec.generate_private_key(ec.SECP256R1())

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps(
            {"type": kind, "challenge": challenge, "origin": self.origin, "crossOrigin": False}
        ).encode()

    def _cose_key(self) -> bytes:
        numbers = self._key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,    # kty: EC2
            3: -7,   # alg: ES256
            -1: 1,   # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def _authenticator_data(self, *, with_credential: bool) -> bytes:
        flags = _USER_PRESENT | _USER_VERIFIED | (_HAS_CREDENTIAL if with_credential else 0)
        data = hashlib.sha256(self.rp_id.encode()).digest()
        data += bytes([flags]) + self.sign_count.to_bytes(4, "big")
        if with_credential:
            data += bytes(16)  # aaguid
            data += len(self.credential_id).to_bytes(2, "big") + self.credential_id
            data += self._cose_key()
        return data

    def create(self, options: dict) -> dict:
        """The answer to registration options (``navigator.credentials.create``)."""
        attestation = cbor2.dumps({
            "fmt": "none",
            "attStmt": {},
            "authData": self._authenticator_data(with_credential=True),
        })
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(self._client_data("webauthn.create", options["challenge"])),
                "attestationObject": b64url(attestation),
                "transports": ["internal"],
            },
        }

    def get(self, options: dict) -> dict:
        """The answer to authentication options (``navigator.credentials.get``)."""
        if self.counts:
            self.sign_count += 1
        authenticator_data = self._authenticator_data(with_credential=False)
        client_data = self._client_data("webauthn.get", options["challenge"])
        signature = self._key.sign(
            authenticator_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256())
        )
        return {
            "id": b64url(self.credential_id),
            "rawId": b64url(self.credential_id),
            "type": "public-key",
            "authenticatorAttachment": "platform",
            "clientExtensionResults": {},
            "response": {
                "clientDataJSON": b64url(client_data),
                "authenticatorData": b64url(authenticator_data),
                "signature": b64url(signature),
            },
        }
