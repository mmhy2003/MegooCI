"""Lightweight async email helper for sending invite notifications.

Falls back gracefully when SMTP is not configured — the invite link is still
returned in the API response so admins can copy-paste it manually.
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.config import get_settings

logger = logging.getLogger(__name__)


def is_smtp_configured() -> bool:
    settings = get_settings()
    return bool(settings.MEGOOCI_SMTP_HOST and settings.MEGOOCI_SMTP_USER)


def send_invite_email(to_email: str, invite_link: str, inviter_name: str | None = None) -> bool:
    """Send an invitation email. Returns True on success, False on failure.

    If SMTP is not configured, logs a warning and returns False (non-fatal).
    """
    settings = get_settings()
    if not is_smtp_configured():
        logger.warning(
            "SMTP not configured — skipping invite email to %s. "
            "Share the invite link manually: %s",
            to_email, invite_link,
        )
        return False

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
    msg["From"] = f"{settings.MEGOOCI_SMTP_FROM_NAME} <{settings.MEGOOCI_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return _send_email(to_email, msg)


def send_password_reset_email(to_email: str, reset_link: str) -> bool:
    """Send a password-reset email. Returns True on success, False on failure."""
    settings = get_settings()
    if not is_smtp_configured():
        logger.warning(
            "SMTP not configured — skipping password-reset email to %s. "
            "Reset link: %s",
            to_email, reset_link,
        )
        return False

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
    msg["From"] = f"{settings.MEGOOCI_SMTP_FROM_NAME} <{settings.MEGOOCI_SMTP_FROM_EMAIL}>"
    msg["To"] = to_email
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    return _send_email(to_email, msg)


def _send_email(to_email: str, msg: MIMEMultipart) -> bool:
    settings = get_settings()
    try:
        if settings.MEGOOCI_SMTP_TLS:
            server = smtplib.SMTP(settings.MEGOOCI_SMTP_HOST, settings.MEGOOCI_SMTP_PORT)
            server.starttls()
        else:
            server = smtplib.SMTP(settings.MEGOOCI_SMTP_HOST, settings.MEGOOCI_SMTP_PORT)

        if settings.MEGOOCI_SMTP_USER:
            server.login(settings.MEGOOCI_SMTP_USER, settings.MEGOOCI_SMTP_PASSWORD)

        server.sendmail(settings.MEGOOCI_SMTP_FROM_EMAIL, to_email, msg.as_string())
        server.quit()
        logger.info("Email sent to %s", to_email)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False
