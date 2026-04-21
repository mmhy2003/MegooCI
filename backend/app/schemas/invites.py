import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class InviteCreate(BaseModel):
    email: EmailStr
    role_id: uuid.UUID


class InviteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role_id: uuid.UUID
    role_name: str | None = None
    status: str
    expires_at: datetime
    created_by: uuid.UUID | None = None
    creator_name: str | None = None
    accepted_at: datetime | None = None
    created_at: datetime


class InviteCreatedResponse(InviteResponse):
    invite_link: str


class AcceptInviteRequest(BaseModel):
    token: str
    name: str
    password: str
