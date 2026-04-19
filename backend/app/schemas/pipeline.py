import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PipelineCreate(BaseModel):
    project_id: uuid.UUID
    name: str
    source_repo_url: str | None = None
    default_branch: str = "main"
    definition_format: str = "yaml"
    yaml_content: str | None = None


class PipelineUpdate(BaseModel):
    name: str | None = None
    source_repo_url: str | None = None
    default_branch: str | None = None
    yaml_content: str | None = None
    enabled: bool | None = None


class PipelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    source_repo_url: str | None
    default_branch: str
    definition_path: str
    definition_format: str
    yaml_content: str | None
    enabled: bool
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime | None
