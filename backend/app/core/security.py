from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


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
