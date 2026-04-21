"""Pydantic schemas for in-app user notifications."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class UserNotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    type: str
    title: str
    body: str
    entity_type: str | None
    entity_id: uuid.UUID | None
    read_at: datetime | None
    created_at: datetime


class UnreadCountResponse(BaseModel):
    count: int
