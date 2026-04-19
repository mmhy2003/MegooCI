import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Secret(Base):
    __tablename__ = "secrets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    secret_type: Mapped[str] = mapped_column(String(20), default="text")
    encrypted_payload: Mapped[bytes] = mapped_column(LargeBinary)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    rotated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    creator: Mapped["User"] = relationship(  # noqa: F821
        back_populates="secrets", foreign_keys=[created_by]
    )

    def __repr__(self) -> str:
        return f"<Secret {self.name} ({self.scope_type})>"


class EnvVar(Base):
    __tablename__ = "env_vars"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scope_type: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255))
    value: Mapped[str] = mapped_column(Text)
    is_secret_ref: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    creator: Mapped["User"] = relationship(  # noqa: F821
        back_populates="env_vars", foreign_keys=[created_by]
    )

    def __repr__(self) -> str:
        return f"<EnvVar {self.name}>"
