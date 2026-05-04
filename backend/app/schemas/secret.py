import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SecretCreate(BaseModel):
    scope_type: str
    scope_id: uuid.UUID | None = None
    name: str
    secret_type: str = "text"
    value: str


class SecretResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    name: str
    secret_type: str
    created_at: datetime
    updated_at: datetime | None


class SecretUpdate(BaseModel):
    name: str | None = None
    value: str | None = None
    scope_type: str | None = None
    scope_id: uuid.UUID | None = None


class EnvVarCreate(BaseModel):
    scope_type: str
    scope_id: uuid.UUID | None = None
    name: str
    value: str


class EnvVarUpdate(BaseModel):
    value: str | None = None
    name: str | None = None
    scope_type: str | None = None
    scope_id: uuid.UUID | None = None


class EnvVarResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID | None
    name: str
    value: str
    is_secret_ref: bool
    created_at: datetime
