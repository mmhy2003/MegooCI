"""Pydantic schemas for notification channels.

Responses never include the decrypted config (tokens, passwords). A redacted
``config_summary`` is returned for UI display (e.g. smtp_host visible, password
masked).
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ChannelType = Literal["email", "slack", "telegram"]

SENSITIVE_CONFIG_KEYS = {"smtp_password", "webhook_url", "bot_token"}


def redact_config(config: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *config* with sensitive values replaced by '••••'."""
    out: dict[str, Any] = {}
    for k, v in config.items():
        if k in SENSITIVE_CONFIG_KEYS and v:
            out[k] = "\u2022\u2022\u2022\u2022"
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------
# NotificationChannel
# --------------------------------------------------------------------------
class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    channel_type: ChannelType
    config: dict[str, Any]


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class NotificationChannelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    channel_type: str
    enabled: bool
    config_summary: dict[str, Any]
    validation_status: str
    last_validated_at: datetime | None
    validation_error: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime | None


class NotificationChannelTestResult(BaseModel):
    ok: bool
    detail: str


# --------------------------------------------------------------------------
# NotificationDelivery
# --------------------------------------------------------------------------
class NotificationDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    channel_id: uuid.UUID
    build_id: uuid.UUID | None
    step_id: uuid.UUID | None
    recipient: str | None
    subject: str | None
    message: str
    status: str
    error: str | None
    sent_at: datetime | None
    created_at: datetime
