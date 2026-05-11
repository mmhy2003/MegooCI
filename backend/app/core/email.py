"""Lightweight email helper for sending invite and password-reset emails.

Email is now configured via NotificationChannel (channel_type='email') in the
admin UI instead of env vars. Falls back gracefully when no email channel is
configured — the invite link is still returned in the API response so admins
can copy-paste it manually.
"""

import asyncio
import base64
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── App logo as inline base64 data URI (defined once, used by all emails) ──
# Derived from frontend/public/icons/icon.svg.  Embedded directly so the
# logo renders without external requests (works in Gmail, Outlook, Apple
# Mail, etc.).  The SVG is read at import time from the repo tree when
# available; in production Docker images the pre-computed fallback is used.
_LOGO_SVG_PATH = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "frontend" / "public" / "icons" / "icon.svg"
)
try:
    _LOGO_SVG_B64 = base64.b64encode(_LOGO_SVG_PATH.read_bytes()).decode()
except Exception:
    # Fallback: first 8 chars are enough for the module to load even if
    # the frontend tree isn't mounted.  A missing logo is non-fatal.
    _LOGO_SVG_B64 = ""

LOGO_DATA_URI = f"data:image/svg+xml;base64,{_LOGO_SVG_B64}" if _LOGO_SVG_B64 else ""


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

    logo_data_uri = LOGO_DATA_URI
    # ── Theme palette (from globals.css dark mode) ──────────────────────
    # background:  hsl(264, 90%, 4%)   → #0d0117
    # card:        hsl(262, 60%, 7%)   → #110923
    # primary:     hsl(176, 100%, 50%) → #00ffee  (neon cyan)
    # accent:      #ff2d95  (magenta, from the SVG)
    # foreground:  hsl(176, 20%, 90%)  → #d9ede9
    # muted-fg:    hsl(262, 14%, 55%)  → #837b96
    # border:      hsl(262, 40%, 15%)  → #1e1535

    html_body = f"""\
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta name="color-scheme" content="dark"/>
  <meta name="supported-color-schemes" content="dark"/>
</head>
<body style="margin: 0; padding: 0; background-color: #0d0117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
  <!-- Outer wrapper for background color -->
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background-color: #0d0117;">
    <tr>
      <td align="center" style="padding: 40px 16px;">

        <!-- Main card -->
        <table role="presentation" cellpadding="0" cellspacing="0" width="560" style="max-width: 560px; width: 100%; background-color: #110923; border: 1px solid #1e1535; border-radius: 16px; overflow: hidden;">

          <!-- Header band with gradient -->
          <tr>
            <td style="background: linear-gradient(135deg, #1a0033 0%, #0d0117 50%, #001a18 100%); padding: 40px 32px 32px; text-align: center; border-bottom: 1px solid #1e1535;">
              <!-- Logo -->
              <img src="{logo_data_uri}" alt="MegooCI" width="72" height="72" style="display: block; margin: 0 auto 20px; border-radius: 16px;"/>
              <h1 style="margin: 0; font-size: 24px; font-weight: 700; color: #00ffee; letter-spacing: -0.02em;">
                You're invited to MegooCI
              </h1>
              <p style="margin: 8px 0 0; font-size: 14px; color: #837b96;">
                Continuous Integration &amp; Delivery Platform
              </p>
            </td>
          </tr>

          <!-- Body content -->
          <tr>
            <td style="padding: 32px 32px 24px;">
              <p style="margin: 0 0 20px; font-size: 15px; line-height: 1.7; color: #d9ede9;">
                {f'<strong style="color: #d9ede9;">{inviter_name}</strong> has invited you' if inviter_name else "You've been invited"} to join
                <strong style="color: #00ffee;">MegooCI</strong>. Accept the invitation below
                to set up your account and start building.
              </p>

              <!-- CTA Button -->
              <div style="margin: 28px 0; text-align: center;">
                <a href="{invite_link}"
                   style="display: inline-block; padding: 14px 36px; background: linear-gradient(135deg, #00ffee 0%, #00b8ff 100%); color: #0d0117; text-decoration: none; border-radius: 10px; font-weight: 700; font-size: 15px; letter-spacing: 0.02em; mso-padding-alt: 14px 36px;">
                  Accept Invitation
                </a>
              </div>

              <!-- Separator -->
              <div style="height: 1px; background: linear-gradient(90deg, transparent 0%, #1e1535 20%, #ff2d9540 50%, #1e1535 80%, transparent 100%); margin: 28px 0;"></div>

              <!-- Expiry info -->
              <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td width="4" style="background: #ff2d95; border-radius: 2px;"></td>
                  <td style="padding: 12px 16px;">
                    <p style="margin: 0; font-size: 13px; color: #837b96; line-height: 1.5;">
                      This invitation expires in <strong style="color: #d9ede9;">{settings.MEGOOCI_INVITE_EXPIRY_HOURS} hours</strong>.
                      If you didn't expect this email, you can safely ignore it.
                    </p>
                  </td>
                </tr>
              </table>

              <!-- Fallback link -->
              <div style="margin-top: 24px; padding: 16px; background-color: #0d0117; border: 1px solid #1e1535; border-radius: 8px;">
                <p style="margin: 0 0 8px; font-size: 12px; color: #837b96; text-transform: uppercase; letter-spacing: 0.08em;">
                  Or copy this link
                </p>
                <p style="margin: 0; font-size: 13px; word-break: break-all;">
                  <a href="{invite_link}" style="color: #00b8ff; text-decoration: none;">{invite_link}</a>
                </p>
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 20px 32px; border-top: 1px solid #1e1535; text-align: center;">
              <p style="margin: 0; font-size: 12px; color: #837b96;">
                &copy; MegooCI &middot; Continuous Integration &amp; Delivery
              </p>
            </td>
          </tr>

        </table>
        <!-- /Main card -->

      </td>
    </tr>
  </table>
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

    logo_data_uri = LOGO_DATA_URI
    html_body = f"""\
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <meta name="color-scheme" content="dark"/>
  <meta name="supported-color-schemes" content="dark"/>
</head>
<body style="margin: 0; padding: 0; background-color: #0d0117; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background-color: #0d0117;">
    <tr>
      <td align="center" style="padding: 40px 16px;">

        <table role="presentation" cellpadding="0" cellspacing="0" width="560" style="max-width: 560px; width: 100%; background-color: #110923; border: 1px solid #1e1535; border-radius: 16px; overflow: hidden;">

          <!-- Header -->
          <tr>
            <td style="background: linear-gradient(135deg, #1a0033 0%, #0d0117 50%, #001a18 100%); padding: 40px 32px 32px; text-align: center; border-bottom: 1px solid #1e1535;">
              <img src="{logo_data_uri}" alt="MegooCI" width="72" height="72" style="display: block; margin: 0 auto 20px; border-radius: 16px;"/>
              <h1 style="margin: 0; font-size: 24px; font-weight: 700; color: #00ffee; letter-spacing: -0.02em;">
                Reset Your Password
              </h1>
              <p style="margin: 8px 0 0; font-size: 14px; color: #837b96;">
                MegooCI Account Security
              </p>
            </td>
          </tr>

          <!-- Body -->
          <tr>
            <td style="padding: 32px 32px 24px;">
              <p style="margin: 0 0 20px; font-size: 15px; line-height: 1.7; color: #d9ede9;">
                We received a request to reset your <strong style="color: #00ffee;">MegooCI</strong>
                password. Click the button below to choose a new one.
              </p>

              <div style="margin: 28px 0; text-align: center;">
                <a href="{reset_link}"
                   style="display: inline-block; padding: 14px 36px; background: linear-gradient(135deg, #00ffee 0%, #00b8ff 100%); color: #0d0117; text-decoration: none; border-radius: 10px; font-weight: 700; font-size: 15px; letter-spacing: 0.02em; mso-padding-alt: 14px 36px;">
                  Reset Password
                </a>
              </div>

              <div style="height: 1px; background: linear-gradient(90deg, transparent 0%, #1e1535 20%, #ff2d9540 50%, #1e1535 80%, transparent 100%); margin: 28px 0;"></div>

              <table role="presentation" cellpadding="0" cellspacing="0" width="100%">
                <tr>
                  <td width="4" style="background: #ff2d95; border-radius: 2px;"></td>
                  <td style="padding: 12px 16px;">
                    <p style="margin: 0; font-size: 13px; color: #837b96; line-height: 1.5;">
                      This link expires in <strong style="color: #d9ede9;">1 hour</strong>.
                      If you didn't request a password reset, you can safely ignore this email.
                    </p>
                  </td>
                </tr>
              </table>

              <div style="margin-top: 24px; padding: 16px; background-color: #0d0117; border: 1px solid #1e1535; border-radius: 8px;">
                <p style="margin: 0 0 8px; font-size: 12px; color: #837b96; text-transform: uppercase; letter-spacing: 0.08em;">
                  Or copy this link
                </p>
                <p style="margin: 0; font-size: 13px; word-break: break-all;">
                  <a href="{reset_link}" style="color: #00b8ff; text-decoration: none;">{reset_link}</a>
                </p>
              </div>
            </td>
          </tr>

          <!-- Footer -->
          <tr>
            <td style="padding: 20px 32px; border-top: 1px solid #1e1535; text-align: center;">
              <p style="margin: 0; font-size: 12px; color: #837b96;">
                &copy; MegooCI &middot; Continuous Integration &amp; Delivery
              </p>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
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
