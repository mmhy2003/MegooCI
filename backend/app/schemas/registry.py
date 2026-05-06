import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Container Repository
# ---------------------------------------------------------------------------

class ContainerRepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    allow_anonymous_pull: bool
    immutable_tags: bool
    quota_bytes: int | None
    used_bytes: int
    created_at: datetime
    updated_at: datetime | None


class ContainerRepositoryUpdate(BaseModel):
    allow_anonymous_pull: bool | None = None
    immutable_tags: bool | None = None
    quota_bytes: int | None = None


# ---------------------------------------------------------------------------
# Container Image
# ---------------------------------------------------------------------------

class ContainerImageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    digest: str
    media_type: str
    size_bytes: int
    config_digest: str | None
    build_id: uuid.UUID | None
    pushed_by: uuid.UUID | None
    created_at: datetime


class ContainerImageDetailResponse(ContainerImageResponse):
    tags: list["ContainerTagResponse"] = []


# ---------------------------------------------------------------------------
# Container Tag
# ---------------------------------------------------------------------------

class ContainerTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    image_id: uuid.UUID
    name: str
    created_at: datetime
    updated_at: datetime | None


# ---------------------------------------------------------------------------
# Deploy Token
# ---------------------------------------------------------------------------

class DeployTokenCreate(BaseModel):
    name: str
    scope: str = "pull"
    expires_in_days: int | None = None


class DeployTokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID | None
    name: str
    token_hint: str
    scope: str
    expires_at: datetime | None
    is_active: bool
    last_used_at: datetime | None
    created_by: uuid.UUID
    created_at: datetime


class DeployTokenCreatedResponse(DeployTokenResponse):
    """Returned only once at creation — includes the raw token."""
    token: str


# ---------------------------------------------------------------------------
# Registry Event
# ---------------------------------------------------------------------------

class RegistryEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repository_id: uuid.UUID
    event_type: str
    digest: str | None
    tag: str | None
    actor_id: uuid.UUID | None
    ip_address: str | None
    created_at: datetime


# ---------------------------------------------------------------------------
# Registry overview (for the UI dashboard)
# ---------------------------------------------------------------------------

class RegistryOverview(BaseModel):
    total_repositories: int
    total_images: int
    total_tags: int
    total_size_bytes: int
