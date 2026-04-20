"""Git provider integration models (PRD §6.16).

- GitProviderConnection: admin-scoped credential to a Git host (GitHub / GitLab /
  generic). Stores the access token Fernet-encrypted. OAuth columns ship in
  Phase 1 but are unused until Phase 2.
- ProjectRepository: per-project link of a repository URL to a connection, with
  a unique webhook slug + bcrypt-hashed webhook secret.
- WebhookDelivery: append-only record of every inbound webhook request, kept for
  debugging and UI display. Deduplicated by (project_repository_id,
  provider_delivery_id) for replay protection.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class GitProviderConnection(Base):
    __tablename__ = "git_provider_connections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True)
    provider_type: Mapped[str] = mapped_column(String(20))  # github|gitlab|generic
    base_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    auth_mode: Mapped[str] = mapped_column(String(10), default="pat")  # pat|oauth

    # Phase 1 stores a PAT here. Phase 2 will store an OAuth access token here
    # and an accompanying refresh token in `encrypted_refresh_token`.
    encrypted_credential: Mapped[bytes] = mapped_column(LargeBinary)
    credential_hint: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # last 4 chars of the token, for UI display

    encrypted_refresh_token: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    oauth_client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    encrypted_oauth_client_secret: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    token_scopes: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    validation_status: Mapped[str] = mapped_column(
        String(10), default="unknown"
    )  # unknown|ok|failed
    last_validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    validation_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    repositories: Mapped[list["ProjectRepository"]] = relationship(
        back_populates="connection"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GitProviderConnection {self.provider_type}:{self.name}>"


class ProjectRepository(Base):
    __tablename__ = "project_repositories"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    connection_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("git_provider_connections.id")
    )

    repo_url: Mapped[str] = mapped_column(String(2048))
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # 24-char random slug used in POST /api/v1/webhooks/git/{slug}
    webhook_slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    # Fernet-encrypted webhook secret. The plaintext is shown to the user
    # exactly once on create/rotate; it must remain recoverable for HMAC
    # verification (GitHub / generic) and for shared-token comparison (GitLab).
    # Column name is historical; stored value is a Fernet ciphertext string.
    webhook_secret_hash: Mapped[str] = mapped_column(String(255))

    last_event_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_event_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # accepted|rejected|duplicate|unparseable

    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    connection: Mapped["GitProviderConnection"] = relationship(
        back_populates="repositories"
    )
    deliveries: Mapped[list["WebhookDelivery"]] = relationship(
        back_populates="repository", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectRepository {self.repo_url}>"


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "project_repository_id",
            "provider_delivery_id",
            name="uq_webhook_delivery_repo_delivery",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_repository_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("project_repositories.id")
    )
    provider_delivery_id: Mapped[str] = mapped_column(String(128))

    event_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)

    signature_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    http_status: Mapped[int] = mapped_column(Integer, default=200)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    repository: Mapped["ProjectRepository"] = relationship(
        back_populates="deliveries"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<WebhookDelivery {self.event_type} {self.commit_sha}>"
