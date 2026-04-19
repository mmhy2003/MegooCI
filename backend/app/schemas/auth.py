import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr


class SignupRequest(BaseModel):
    email: EmailStr
    name: str
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    is_admin: bool
    is_active: bool
    auth_provider: str
    created_at: datetime
