"""Lightweight email helper for sending invite and password-reset emails.

Email is now configured via NotificationChannel (channel_type='email') in the
admin UI instead of env vars. Falls back gracefully when no email channel is
configured — the invite link is still returned in the API response so admins
can copy-paste it manually.
"""

import asyncio
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)


async def _get_email_config(db: AsyncSession) -> dict | None:
    """Load SMTP config from the first enabled email notification channel.

    Uses the *caller's* already-open async session — no new event loop, no
    cross-loop asyncpg connection sharing.

    Returns the decrypted config dict, or None if no email channel exists.
    """
    from app.models.notification import NotificationChannel
    from app.services.notification_service import decrypt_channel_config

    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.channel_type == "email",
            NotificationChannel.enabled.is_(True),
        ).limit(1)
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        return None

    return decrypt_channel_config(channel.config_encrypted)


async def is_smtp_configured(db: AsyncSession) -> bool:
    config = await _get_email_config(db)
    return config is not None and bool(config.get("smtp_host"))


async def send_invite_email(
    db: AsyncSession,
    to_email: str,
    invite_link: str,
    inviter_name: str | None = None,
) -> bool:
    """Send an invitation email. Returns True on success, False on failure."""
    config = await _get_email_config(db)
    if not config or not config.get("smtp_host"):
        logger.warning(
            "No email channel configured — skipping invite email to %s. "
            "Share the invite link manually: %s",
            to_email, invite_link,
        )
        return False

    settings = get_settings()
    from_email = config.get("from_email", "noreply@megooci.local")
    from_name = config.get("from_name", "MegooCI")

    subject = "You've been invited to MegooCI"
    invited_by = f" by {inviter_name}" if inviter_name else ""

    html_body = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="color: #1a1a1a;">You're invited to MegooCI</h2>
  <p style="color: #444; line-height: 1.6;">
    You've been invited{invited_by} to join MegooCI. Click the button below
    to create your account.
  </p>
  <div style="margin: 32px 0;">
    <a href="{invite_link}"
       style="display: inline-block; padding: 12px 24px; background: #2563eb; color: #fff; text-decoration: none; border-radius: 8px; font-weight: 600;">
      Accept Invitation
    </a>
  </div>
  <p style="color: #888; font-size: 13px;">
    This link expires in {settings.MEGOOCI_INVITE_EXPIRY_HOURS} hours.
    If you didn't expect this email, you can safely ignore it.
  </p>
  <p style="color: #888; font-size: 13px;">
    Or copy this link: <br/>
    <code style="word-break: break-all;">{invite_link}</code>
  </p>
</body>
</html>"""

    text_body = (
        f"You've been invited{invited_by} to join MegooCI.\n\n"
        f"Accept your invitation: {invite_link}\n\n"
        f"This link expires in {settings.MEGOOCI_INVITE_EXPIRY_HOURS} hours."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return await _send_email_async(config, to_email, msg)


async def send_password_reset_email(
    db: AsyncSession,
    to_email: str,
    reset_link: str,
) -> bool:
    """Send a password-reset email. Returns True on success, False on failure."""
    config = await _get_email_config(db)
    if not config or not config.get("smtp_host"):
        logger.warning(
            "No email channel configured — skipping password-reset email to %s. "
            "Reset link: %s",
            to_email, reset_link,
        )
        return False

    from_email = config.get("from_email", "noreply@megooci.local")
    from_name = config.get("from_name", "MegooCI")

    subject = "Reset your MegooCI password"

    html_body = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 560px; margin: 0 auto; padding: 24px;">
  <h2 style="color: #1a1a1a;">Reset your password</h2>
  <p style="color: #444; line-height: 1.6;">
    We received a request to reset your MegooCI password. Click the button
    below to choose a new one.
  </p>
  <div style="margin: 32px 0;">
    <a href="{reset_link}"
       style="display: inline-block; padding: 12px 24px; background: #2563eb; color: #fff; text-decoration: none; border-radius: 8px; font-weight: 600;">
      Reset Password
    </a>
  </div>
  <p style="color: #888; font-size: 13px;">
    This link expires in 1 hour.
    If you didn't request a password reset, you can safely ignore this email.
  </p>
  <p style="color: #888; font-size: 13px;">
    Or copy this link: <br/>
    <code style="word-break: break-all;">{reset_link}</code>
  </p>
</body>
</html>"""

    text_body = (
        "We received a request to reset your MegooCI password.\n\n"
        f"Reset your password: {reset_link}\n\n"
        "This link expires in 1 hour. If you didn't request this, ignore this email."
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return await _send_email_async(config, to_email, msg)


async def _send_email_async(config: dict, to_email: str, msg: MIMEMultipart) -> bool:
    """Run the blocking smtplib call in a thread-pool executor so it doesn't
    block the uvicorn event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _send_email_sync, config, to_email, msg)


def _send_email_sync(config: dict, to_email: str, msg: MIMEMultipart) -> bool:
    """Blocking SMTP send — always called from a thread, never from the event loop."""
    try:
        host = config["smtp_host"]
        port = int(config.get("smtp_port", 587))
        use_tls = config.get("tls", True)
        user = config.get("smtp_user", "")
        password = config.get("smtp_password", "")
        from_email = config.get("from_email", "noreply@megooci.local")

        server = smtplib.SMTP(host, port)
        if use_tls:
            server.starttls()

        if user:
            server.login(user, password)

        server.sendmail(from_email, to_email, msg.as_string())
        server.quit()
        logger.info("Email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False
