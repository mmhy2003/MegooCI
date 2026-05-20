import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    build_id: uuid.UUID
    relative_path: str
    size_bytes: int
    checksum_sha256: str
    retention_until: datetime | None
    created_at: datetime


class ArtifactListItem(BaseModel):
    """Enriched artifact entry for the global list (includes build + project context)."""

    id: uuid.UUID
    build_id: uuid.UUID
    build_number: int
    pipeline_id: uuid.UUID
    pipeline_name: str
    project_id: uuid.UUID
    project_name: str
    relative_path: str
    size_bytes: int
    checksum_sha256: str
    retention_until: datetime | None
    created_at: datetime
