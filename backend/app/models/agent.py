import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), unique=True)
    labels: Mapped[list] = mapped_column(JSON, default=list)
    os: Mapped[str | None] = mapped_column(String(50), nullable=True)
    arch: Mapped[str | None] = mapped_column(String(50), nullable=True)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="offline")
    # Operators can disable an agent (e.g. for maintenance) without deleting
    # it. Disabled agents stay registered but the dispatcher skips them.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true", default=True
    )

    # Agent authentication (PRD §6.3 / F-3.4).
    #
    # `token_hash` is a bcrypt hash of the plaintext token we return once at
    # registration / rotation. The plaintext is never stored server-side.
    # `token_prefix` is the first 12 characters of the plaintext (e.g.
    # "megci_agt_Ab") — safe to display so operators can tell tokens apart in
    # the UI and in logs.
    token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_prefix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    token_issued_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Runtime info reported by the agent when it connects.
    agent_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Which build this agent is currently executing (NULL = idle).
    # Set when execute_build claims the agent, cleared when the build
    # finishes or is cancelled. The dispatcher skips agents where this
    # is not NULL.
    current_build_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("builds.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<Agent {self.name} ({self.status})>"
