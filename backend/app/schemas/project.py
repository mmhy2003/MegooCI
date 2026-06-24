import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ProjectMemberResponse(BaseModel):
    user_role_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    name: str
    role_name: str


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    parent_id: uuid.UUID | None = None


class ProjectUpdate(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    parent_id: uuid.UUID | None
    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime | None
