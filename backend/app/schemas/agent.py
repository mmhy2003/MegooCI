import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    labels: list[str] = Field(default_factory=list)
    os: str | None = None
    arch: str | None = None
    capacity: int = Field(default=1, ge=1, le=64)
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    labels: list[str] | None = None
    os: str | None = None
    arch: str | None = None
    capacity: int | None = Field(default=None, ge=1, le=64)
    enabled: bool | None = None


class HeartbeatRequest(BaseModel):
    """Optional payload the agent sends with its periodic heartbeat.

    Empty-body heartbeats are also accepted for compatibility.
    """

    version: str | None = None


class CurrentBuildInfo(BaseModel):
    """The build an agent is currently reserved for.

    Computed at read time from ``Agent.current_build_id`` and surfaced on the
    agents page so the card can link to the running build. Never stored.
    """

    id: uuid.UUID
    number: int
    pipeline_name: str


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    labels: list[str]
    os: str | None
    arch: str | None
    capacity: int
    enabled: bool = True
    last_seen_at: datetime | None
    status: str
    # Effective runtime build, present only when the agent is online and
    # holding a reservation. Computed in the API layer; never persisted.
    current_build: CurrentBuildInfo | None = None
    # Token metadata — the plaintext token is never returned here.
    token_prefix: str | None = None
    token_issued_at: datetime | None = None
    # Runtime info.
    agent_version: str | None = None
    connected_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class AgentRegistrationResponse(AgentResponse):
    """Response when registering (or rotating) — includes the plaintext token.

    The plaintext token is returned exactly once and is not recoverable.
    """

    registration_token: str
