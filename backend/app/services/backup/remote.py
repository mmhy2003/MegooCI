"""The optional copy of each backup to S3-compatible storage."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.services.backup.settings import RemoteConfig

_TEST_OBJECT = ".megooci-connection-test"
_MAX_REASON = 300
CONNECT_TIMEOUT_SECONDS = 10
READ_TIMEOUT_SECONDS = 60
MAX_ATTEMPTS = 2


class RemoteError(Exception):
    """The storage refused or could not be reached. The message is the
    storage's own reason, for the administrator."""


def _client(config: RemoteConfig) -> Any:
    # Imported here: boto3 takes a noticeable moment to load, and most
    # servers never turn the remote copy on.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        endpoint_url=config.endpoint_url or None,
        region_name=config.region or "us-east-1",
        aws_access_key_id=config.access_key_id,
        aws_secret_access_key=config.secret_access_key,
        config=Config(
            signature_version="s3v4",
            # Other S3 implementations are reached by address, not by a
            # bucket-named host.
            s3={"addressing_style": "path"} if config.endpoint_url else {},
            connect_timeout=CONNECT_TIMEOUT_SECONDS,
            read_timeout=READ_TIMEOUT_SECONDS,
            retries={"max_attempts": MAX_ATTEMPTS},
        ),
    )


def _reason(exc: Exception) -> str:
    error = getattr(exc, "response", {}).get("Error", {}) if hasattr(exc, "response") else {}
    if error.get("Code"):
        text = f"{error['Code']}: {error.get('Message', '')}".strip().rstrip(":")
    else:
        text = str(exc) or type(exc).__name__
    return text[:_MAX_REASON]


async def _run(action: Callable[[], None]) -> None:
    try:
        await asyncio.to_thread(action)
    except Exception as exc:
        raise RemoteError(_reason(exc)) from exc


async def upload(config: RemoteConfig, name: str, data: bytes) -> None:
    def action() -> None:
        _client(config).put_object(
            Bucket=config.bucket, Key=config.object_key(name), Body=data,
            ContentType="application/octet-stream",
        )

    await _run(action)


async def delete(config: RemoteConfig, name: str) -> None:
    def action() -> None:
        _client(config).delete_object(Bucket=config.bucket, Key=config.object_key(name))

    await _run(action)


async def check_connection(config: RemoteConfig) -> None:
    """Write and delete a small object, as a backup would."""
    def action() -> None:
        client = _client(config)
        key = config.object_key(_TEST_OBJECT)
        client.put_object(Bucket=config.bucket, Key=key, Body=b"megooci")
        client.delete_object(Bucket=config.bucket, Key=key)

    await _run(action)
