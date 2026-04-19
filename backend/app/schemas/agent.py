import secrets
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    labels: list[str] = Field(default_factory=list)
    os: str | None = None
    arch: str | None = None
    capacity: int = Field(default=1, ge=1, le=64)


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    labels: list[str] | None = None
    os: str | None = None
    arch: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=64)


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    labels: list[str]
    os: str | None
    arch: str | None
    capacity: int
    last_seen_at: datetime | None
    status: str
    created_at: datetime
    updated_at: datetime | None = None


class AgentRegistrationResponse(AgentResponse):
    """Response when registering a new agent — includes a one-time token."""

    registration_token: str


def generate_registration_token() -> str:
    """URL-safe random token used by the agent to authenticate back to the controller."""
    return secrets.token_urlsafe(32)
