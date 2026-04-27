from datetime import datetime, timedelta, timezone

import bcrypt
from cryptography.fernet import Fernet
from jose import JWTError, jwt

from app.config import get_settings


def _truncate_to_72_bytes(password: str) -> bytes:
    """bcrypt has a hard 72-byte input limit; truncate safely."""
    return password.encode("utf-8")[:72]


def hash_password(password: str) -> str:
    hashed = bcrypt.hashpw(_truncate_to_72_bytes(password), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_truncate_to_72_bytes(plain), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta
        or timedelta(minutes=settings.MEGOOCI_JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(
        to_encode,
        settings.MEGOOCI_JWT_SECRET,
        algorithm=settings.MEGOOCI_JWT_ALGORITHM,
    )


def create_refresh_token(data: dict) -> str:
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(
        days=settings.MEGOOCI_JWT_REFRESH_TOKEN_EXPIRE_DAYS
    )
    to_encode.update({"exp": expire, "type": "refresh"})
    return jwt.encode(
        to_encode,
        settings.MEGOOCI_JWT_SECRET,
        algorithm=settings.MEGOOCI_JWT_ALGORITHM,
    )


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.MEGOOCI_JWT_SECRET,
            algorithms=[settings.MEGOOCI_JWT_ALGORITHM],
        )
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc


def _derive_fernet_key(key: str) -> bytes:
    """Derive a Fernet-compatible 32-byte URL-safe base64 key from an arbitrary string."""
    import base64
    import hashlib

    raw = hashlib.sha256(key.encode()).digest()
    return base64.urlsafe_b64encode(raw)


def encrypt_secret(plaintext: str, key: str) -> bytes:
    fernet = Fernet(_derive_fernet_key(key))
    return fernet.encrypt(plaintext.encode())


def decrypt_secret(ciphertext: bytes, key: str) -> str:
    fernet = Fernet(_derive_fernet_key(key))
    return fernet.decrypt(ciphertext).decode()


# ----------------------------------------------------------------------------
# Webhook secret helpers (PRD §6.16)
# ----------------------------------------------------------------------------
def generate_webhook_slug(length: int = 24) -> str:
    """Generate a URL-safe random slug for a webhook endpoint.

    The slug is part of the publicly-visible webhook URL; it is not a
    credential, but we make it long enough to prevent guessing.
    """
    import secrets

    return secrets.token_urlsafe(length)[:length]


def generate_webhook_secret(length: int = 32) -> str:
    """Generate a high-entropy URL-safe token used as the HMAC / shared-secret
    on inbound webhook deliveries. Returned to the user exactly once.
    """
    import secrets

    return secrets.token_urlsafe(length)


def encrypt_webhook_secret(secret: str, key: str) -> str:
    """Encrypt a webhook secret at rest.

    The secret must be recoverable in plaintext at webhook-verification time:
    GitHub and our generic adapter verify an HMAC of the raw body using the
    secret as the HMAC key, and GitLab compares the secret to an
    `X-Gitlab-Token` header. bcrypt (one-way) would break HMAC verification,
    so we use Fernet symmetric encryption keyed from `MEGOOCI_SECRET_KEY`.

    Returns a Fernet token as a URL-safe base64 string, safely storable in a
    `String(255)` column.
    """
    fernet = Fernet(_derive_fernet_key(key))
    return fernet.encrypt(secret.encode()).decode("ascii")


def decrypt_webhook_secret(token: str, key: str) -> str:
    """Inverse of `encrypt_webhook_secret`. Raises `InvalidToken` if the
    stored ciphertext is tampered with or the master key was rotated.
    """
    fernet = Fernet(_derive_fernet_key(key))
    return fernet.decrypt(token.encode("ascii")).decode()


def credential_hint(token: str) -> str:
    """Return the last 4 characters of a credential for UI display."""
    if not token:
        return ""
    return token[-4:] if len(token) > 4 else "*" * len(token)


# ----------------------------------------------------------------------------
# Agent tokens (PRD §6.3 / F-3.4)
# ----------------------------------------------------------------------------
_AGENT_TOKEN_PREFIX = "megci_agt_"


def generate_agent_token() -> str:
    """Generate a URL-safe random token for an agent to authenticate with.

    The `megci_agt_` prefix makes tokens self-describing (operators can tell
    the difference between an agent token and, say, a provider PAT at a
    glance) and makes it easy for future secret-scanning tools to catch
    accidental commits.
    """
    import secrets

    return _AGENT_TOKEN_PREFIX + secrets.token_urlsafe(32)


def agent_token_prefix(token: str) -> str:
    """Return the first 12 characters of a token for non-sensitive display."""
    return token[:12]


def hash_agent_token(token: str) -> str:
    """bcrypt-hash an agent token for database storage."""
    hashed = bcrypt.hashpw(_truncate_to_72_bytes(token), bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_agent_token(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            _truncate_to_72_bytes(plain), hashed.encode("utf-8")
        )
    except ValueError:
        return False


# ----------------------------------------------------------------------------
# Signed artifact download URLs
# ----------------------------------------------------------------------------
_SIGNED_URL_SEP = ":"


def generate_signed_artifact_url(
    artifact_id: str,
    secret_key: str,
    ttl_seconds: int = 300,
) -> str:
    """Create a short-lived HMAC-signed token for an artifact download.

    Token format: ``<artifact_id>:<expires_unix>:<hmac_hex>``
    """
    import hmac as _hmac
    import time

    expires = int(time.time()) + ttl_seconds
    message = f"{artifact_id}{_SIGNED_URL_SEP}{expires}"
    sig = _hmac.new(
        secret_key.encode(), message.encode(), "sha256"
    ).hexdigest()
    return f"{message}{_SIGNED_URL_SEP}{sig}"


def verify_signed_artifact_url(token: str, secret_key: str) -> str | None:
    """Verify a signed artifact token.

    Returns the artifact_id if valid and not expired, else ``None``.
    """
    import hmac as _hmac
    import time

    parts = token.split(_SIGNED_URL_SEP)
    if len(parts) != 3:
        return None
    artifact_id, expires_str, sig = parts
    try:
        expires = int(expires_str)
    except ValueError:
        return None

    if time.time() > expires:
        return None

    message = f"{artifact_id}{_SIGNED_URL_SEP}{expires_str}"
    expected = _hmac.new(
        secret_key.encode(), message.encode(), "sha256"
    ).hexdigest()
    if not _hmac.compare_digest(sig, expected):
        return None
    return artifact_id


# ----------------------------------------------------------------------------
# Personal Access Tokens (PATs)
# ----------------------------------------------------------------------------
_PAT_PREFIX = "megci_pat_"


def generate_pat() -> str:
    """Generate a Personal Access Token.

    Format: ``megci_pat_<44-char-urlsafe-random>``
    """
    import secrets as _secrets

    return _PAT_PREFIX + _secrets.token_urlsafe(33)


def hash_pat(token: str) -> str:
    """SHA-256 hex digest of a PAT — used for DB lookup."""
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()


def pat_hint(token: str) -> str:
    """Return the first 12 chars of a PAT for UI display."""
    return token[:12] if len(token) > 12 else token


def is_pat(token: str) -> bool:
    """Check whether a bearer token is a PAT (vs a JWT)."""
    return token.startswith(_PAT_PREFIX)

