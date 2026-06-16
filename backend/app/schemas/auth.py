import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class SetupIn(BaseModel):
    email: EmailStr
    name: str
    # First superadmin: enforce a minimum length (and cap to bound Argon2 work). LoginIn below is a
    # re-auth against an existing password and must NOT carry min_length (legacy passwords may be short).
    password: str = Field(min_length=12, max_length=1024)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=1024)


class MeOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
    mfa_setup_required: bool = False


class RememberDeviceInfo(BaseModel):
    enabled: bool  # whether the org allows "remember this device"
    days: int  # how long a trusted device skips the second factor


class LoginOut(BaseModel):
    status: str  # "ok" | "mfa_required" | "mfa_setup_required"
    user: MeOut | None = None
    # On "mfa_required": which second factors the user can satisfy the challenge with
    # (e.g. ["totp", "webauthn"]) so the SPA shows the right options. None otherwise.
    methods: list[str] | None = None
    # On "mfa_required": whether to offer the "remember this device" checkbox and for how many days.
    remember_device: RememberDeviceInfo | None = None


class SessionInfo(BaseModel):
    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    user_agent: str | None
    current: bool
