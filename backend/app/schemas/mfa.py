import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PasswordIn(BaseModel):
    password: str = Field(max_length=1024)


class CodeIn(BaseModel):
    code: str = Field(max_length=128)
    # Opt-in "remember this device": skip the second factor on this device for N days (N from settings).
    remember_device: bool = False


class SetupOut(BaseModel):
    otpauth_uri: str
    secret: str


class RecoveryOut(BaseModel):
    recovery_codes: list[str]


class WebAuthnStatus(BaseModel):
    configured: bool  # the RP (rp_id + origin) is set -> passkey registration is offered
    credentials: int  # how many passkeys the user has registered


class MfaStatusOut(BaseModel):
    enabled: bool
    recovery_codes_remaining: int
    webauthn: WebAuthnStatus


class WebAuthnRegisterCompleteIn(BaseModel):
    # The browser's PublicKeyCredential JSON from navigator.credentials.create(), passed verbatim to
    # py_webauthn for verification. An optional user label + the authenticator's transport hints.
    credential: dict[str, Any]
    name: str = Field(default="", max_length=128)
    transports: list[str] | None = None


class WebAuthnCredentialOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None = None


class WebAuthnLoginCompleteIn(BaseModel):
    # The browser's PublicKeyCredential JSON from navigator.credentials.get().
    credential: dict[str, Any]
    remember_device: bool = False


class MfaPolicyOut(BaseModel):
    mode: str


class MfaPolicyIn(BaseModel):
    mode: str


class TrustedDeviceOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    user_agent: str | None = None
    ip: str | None = None
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
