import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id")
    )
    # Optional link to a project-scoped GitProviderConnection-backed repository
    # (PRD §6.16). When set, the Pipeline inherits repo_url + default_branch
    # from the ProjectRepository row. `source_repo_url` stays for back-compat.
    project_repository_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("project_repositories.id"),
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(255))
    source_repo_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    definition_path: Mapped[str] = mapped_column(
        String(1024), default="megooci.yaml"
    )
    definition_format: Mapped[str] = mapped_column(String(20), default="yaml")
    yaml_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    project: Mapped["Project"] = relationship(  # noqa: F821
        back_populates="pipelines"
    )
    creator: Mapped["User"] = relationship(  # noqa: F821
        back_populates="pipelines", foreign_keys=[created_by]
    )
    builds: Mapped[list["Build"]] = relationship(  # noqa: F821
        back_populates="pipeline"
    )
    triggers: Mapped[list["Trigger"]] = relationship(  # noqa: F821
        back_populates="pipeline"
    )
    webhook_endpoints: Mapped[list["WebhookEndpoint"]] = relationship(  # noqa: F821
        back_populates="pipeline"
    )

    def __repr__(self) -> str:
        return f"<Pipeline {self.name}>"
