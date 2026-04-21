import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RoleBase(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str] = []


class RoleCreate(RoleBase):
    pass


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    permissions: list[str] | None = None


class RoleResponse(RoleBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    is_system: bool
    created_at: datetime
    updated_at: datetime | None = None


class UserRoleAssign(BaseModel):
    role_id: uuid.UUID
    scope_type: str = "global"
    scope_id: uuid.UUID | None = None


class UserRoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    role_id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None = None
    role_name: str | None = None
    created_at: datetime
