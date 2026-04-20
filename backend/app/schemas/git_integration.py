"""Pydantic schemas for Git provider integration (PRD §6.16).

Responses never include the decrypted credential / webhook secret. Plaintext
webhook secrets are only returned from create + rotate endpoints (see
`ProjectRepositoryWithSecretResponse`).
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderType = Literal["github", "gitlab", "generic"]
AuthMode = Literal["pat", "oauth"]


# ----------------------------------------------------------------------------
# GitProviderConnection
# ----------------------------------------------------------------------------
class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    provider_type: ProviderType
    base_url: str | None = Field(default=None, max_length=2048)
    # Phase 1 only accepts "pat"; column-level OAuth support ships in Phase 2.
    auth_mode: AuthMode = "pat"
    credential: str = Field(min_length=1, max_length=4096)


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_url: str | None = Field(default=None, max_length=2048)
    # Provided only when rotating the token.
    credential: str | None = Field(default=None, min_length=1, max_length=4096)


class ConnectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    provider_type: str
    base_url: str | None
    auth_mode: str
    credential_hint: str | None
    validation_status: str
    last_validated_at: datetime | None
    validation_error: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime | None


class ConnectionTestResult(BaseModel):
    ok: bool
    status: str           # "ok" | "failed"
    detail: str           # human-readable message
    http_status: int | None = None
    latency_ms: int | None = None


# ----------------------------------------------------------------------------
# ProjectRepository
# ----------------------------------------------------------------------------
class ProjectRepositoryCreate(BaseModel):
    connection_id: uuid.UUID
    repo_url: str = Field(min_length=1, max_length=2048)
    default_branch: str = Field(default="main", min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class ProjectRepositoryUpdate(BaseModel):
    default_branch: str | None = Field(default=None, min_length=1, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class ProjectRepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    connection_id: uuid.UUID
    repo_url: str
    default_branch: str
    display_name: str | None
    webhook_slug: str
    last_event_at: datetime | None
    last_event_status: str | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime | None


class ProjectRepositoryWithSecretResponse(ProjectRepositoryResponse):
    """Extends ProjectRepositoryResponse with the one-time plaintext webhook
    secret + the fully-qualified webhook URL. Only returned on create / rotate.
    """

    webhook_secret: str
    webhook_url: str


# ----------------------------------------------------------------------------
# WebhookDelivery
# ----------------------------------------------------------------------------
class WebhookDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_repository_id: uuid.UUID
    provider_delivery_id: str
    event_type: str | None
    branch: str | None
    commit_sha: str | None
    author: str | None
    signature_valid: bool
    http_status: int
    error: str | None
    payload_excerpt: str | None
    received_at: datetime
    processed_at: datetime | None
