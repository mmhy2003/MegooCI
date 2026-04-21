"""Notification delivery service.

Dispatches messages to email, Slack, and Telegram channels configured via the
admin UI. Each delivery is persisted in the notification_deliveries table for
audit purposes.
"""

import asyncio
import json
import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_secret, encrypt_secret
from app.models.notification import NotificationChannel, NotificationDelivery

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# Config helpers
# --------------------------------------------------------------------------

def encrypt_channel_config(config: dict[str, Any]) -> bytes:
    """Fernet-encrypt a channel config dict for DB storage."""
    settings = get_settings()
    plaintext = json.dumps(config)
    return encrypt_secret(plaintext, settings.MEGOOCI_SECRET_KEY)


def decrypt_channel_config(encrypted: bytes) -> dict[str, Any]:
    """Decrypt a Fernet-encrypted channel config from the DB."""
    settings = get_settings()
    plaintext = decrypt_secret(encrypted, settings.MEGOOCI_SECRET_KEY)
    return json.loads(plaintext)


def validate_channel_config(channel_type: str, config: dict[str, Any]) -> list[str]:
    """Return a list of validation errors (empty = valid)."""
    errors: list[str] = []

    if channel_type == "email":
        if not config.get("smtp_host"):
            errors.append("smtp_host is required")
        if not config.get("smtp_port"):
            errors.append("smtp_port is required")
        if not config.get("from_email"):
            errors.append("from_email is required")
    elif channel_type == "slack":
        if not config.get("webhook_url"):
            errors.append("webhook_url is required")
    elif channel_type == "telegram":
        if not config.get("bot_token"):
            errors.append("bot_token is required")
        if not config.get("default_chat_id"):
            errors.append("default_chat_id is required")
    else:
        errors.append(f"Unknown channel type: {channel_type}")

    return errors


# --------------------------------------------------------------------------
# Template rendering
# --------------------------------------------------------------------------

def render_message(
    template: str,
    context: dict[str, Any],
) -> str:
    """Replace ``${{ key }}`` placeholders in *template* with values from *context*.

    Supports flat keys like ``build.status``, ``build.number``, ``pipeline.name``.
    """
    result = template
    for key, value in context.items():
        result = result.replace(f"${{{{ {key} }}}}", str(value))
    return result


# --------------------------------------------------------------------------
# Senders
# --------------------------------------------------------------------------

def _send_email_sync(
    config: dict[str, Any],
    to_email: str,
    subject: str,
    body: str,
) -> None:
    host = config["smtp_host"]
    port = int(config.get("smtp_port", 587))
    user = config.get("smtp_user", "")
    password = config.get("smtp_password", "")
    from_email = config.get("from_email", "noreply@megooci.local")
    from_name = config.get("from_name", "MegooCI")
    use_tls = config.get("tls", True)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject or "MegooCI Notification"
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(body, "plain"))
    msg.attach(MIMEText(f"<html><body><pre>{body}</pre></body></html>", "html"))

    server = smtplib.SMTP(host, port)
    try:
        if use_tls:
            server.starttls()
        if user:
            server.login(user, password)
        server.sendmail(from_email, to_email, msg.as_string())
    finally:
        server.quit()


async def _send_slack(config: dict[str, Any], message: str, recipient: str | None) -> None:
    webhook_url = config["webhook_url"]
    payload: dict[str, Any] = {"text": message}
    if recipient:
        payload["channel"] = recipient

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(webhook_url, json=payload)
        resp.raise_for_status()


async def _send_telegram(config: dict[str, Any], message: str, recipient: str | None) -> None:
    bot_token = config["bot_token"]
    chat_id = recipient or config.get("default_chat_id", "")
    if not chat_id:
        raise ValueError("No chat_id specified and no default_chat_id configured")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

async def send_notification(
    db: AsyncSession,
    channel_id: uuid.UUID,
    message: str,
    *,
    subject: str | None = None,
    recipient: str | None = None,
    build_id: uuid.UUID | None = None,
    step_id: uuid.UUID | None = None,
) -> NotificationDelivery:
    """Send a notification through the given channel and persist a delivery record."""
    channel = await db.get(NotificationChannel, channel_id)
    if channel is None:
        raise ValueError(f"Notification channel {channel_id} not found")
    if not channel.enabled:
        raise ValueError(f"Notification channel '{channel.name}' is disabled")

    config = decrypt_channel_config(channel.config_encrypted)

    delivery = NotificationDelivery(
        channel_id=channel_id,
        build_id=build_id,
        step_id=step_id,
        recipient=recipient,
        subject=subject,
        message=message,
        status="pending",
    )
    db.add(delivery)
    await db.flush()

    try:
        if channel.channel_type == "email":
            to = recipient or config.get("from_email", "")
            if not to:
                raise ValueError("No recipient specified for email notification")
            await asyncio.to_thread(
                _send_email_sync, config, to, subject or "MegooCI Notification", message
            )

        elif channel.channel_type == "slack":
            await _send_slack(config, message, recipient)

        elif channel.channel_type == "telegram":
            await _send_telegram(config, message, recipient)

        else:
            raise ValueError(f"Unknown channel type: {channel.channel_type}")

        delivery.status = "sent"
        delivery.sent_at = datetime.now(timezone.utc)

    except Exception as exc:
        logger.exception("Notification delivery failed for channel %s", channel.name)
        delivery.status = "failed"
        delivery.error = str(exc)[:2000]

    await db.commit()
    return delivery


async def send_notification_by_name(
    db: AsyncSession,
    channel_name: str,
    message: str,
    **kwargs: Any,
) -> NotificationDelivery:
    """Look up a channel by name and send through it."""
    result = await db.execute(
        select(NotificationChannel).where(NotificationChannel.name == channel_name)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise ValueError(f"Notification channel '{channel_name}' not found")
    return await send_notification(db, channel.id, message, **kwargs)


async def test_channel(db: AsyncSession, channel_id: uuid.UUID) -> tuple[bool, str]:
    """Send a test message and return (ok, detail)."""
    try:
        delivery = await send_notification(
            db,
            channel_id,
            message="This is a test notification from MegooCI.",
            subject="MegooCI Test Notification",
        )
        if delivery.status == "sent":
            return True, "Test notification sent successfully"
        return False, delivery.error or "Delivery failed"
    except Exception as exc:
        return False, str(exc)


async def get_email_channel(db: AsyncSession) -> NotificationChannel | None:
    """Return the first enabled email channel, or None."""
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.channel_type == "email",
            NotificationChannel.enabled.is_(True),
        ).limit(1)
    )
    return result.scalar_one_or_none()
