import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(50), default="local")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    projects: Mapped[list["Project"]] = relationship(  # noqa: F821
        back_populates="creator", foreign_keys="Project.created_by"
    )
    pipelines: Mapped[list["Pipeline"]] = relationship(  # noqa: F821
        back_populates="creator", foreign_keys="Pipeline.created_by"
    )
    builds: Mapped[list["Build"]] = relationship(  # noqa: F821
        back_populates="triggerer", foreign_keys="Build.triggered_by"
    )
    audit_entries: Mapped[list["AuditLogEntry"]] = relationship(  # noqa: F821
        back_populates="actor"
    )
    secrets: Mapped[list["Secret"]] = relationship(  # noqa: F821
        back_populates="creator", foreign_keys="Secret.created_by"
    )
    env_vars: Mapped[list["EnvVar"]] = relationship(  # noqa: F821
        back_populates="creator", foreign_keys="EnvVar.created_by"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"
