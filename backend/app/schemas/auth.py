import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr


class SetupIn(BaseModel):
    email: EmailStr
    name: str
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
    mfa_setup_required: bool = False


class LoginOut(BaseModel):
    status: str  # "ok" | "mfa_required" | "mfa_setup_required"
    user: MeOut | None = None


class SessionInfo(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool
