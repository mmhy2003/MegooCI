import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class UserRoleInfo(BaseModel):
    id: str
    role_id: str
    role_name: str | None = None
    scope_type: str
    scope_id: str | None = None
    project_name: str | None = None


class UserDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    is_admin: bool
    is_active: bool
    auth_provider: str
    created_at: datetime
    updated_at: datetime | None = None
    roles: list[dict] = []


class UserCreateRequest(BaseModel):
    email: EmailStr
    name: str
    role_id: uuid.UUID


class UserCreatedResponse(UserDetailResponse):
    generated_password: str


class UserUpdateRequest(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    is_admin: bool | None = None


class UserRoleUpdateRequest(BaseModel):
    role_id: uuid.UUID
