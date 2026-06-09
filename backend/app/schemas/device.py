import uuid
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator


def _validate_base_url_syntax(v: str) -> str:
    # Controllo SINTATTICO (NO DNS): https-only, niente userinfo, host presente.
    parts = urlsplit(v)
    if parts.scheme != "https":
        raise ValueError("base_url deve usare https")
    if parts.username or parts.password:
        raise ValueError("base_url non deve contenere credenziali")
    if not parts.hostname:
        raise ValueError("base_url deve avere un host")
    return v


class DeviceIn(BaseModel):
    name: str
    base_url: str
    api_key: str
    api_secret: str
    verify_tls: bool = True
    tls_fingerprint: str | None = None
    site: str | None = None
    tags: list[str] = Field(default_factory=list)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str) -> str:
        return _validate_base_url_syntax(v)


class DeviceUpdateIn(BaseModel):
    name: str | None = None
    base_url: str | None = None
    verify_tls: bool | None = None
    tls_fingerprint: str | None = None
    site: str | None = None
    tags: list[str] | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_base_url_syntax(v)


class RotateSecretIn(BaseModel):
    api_key: str
    api_secret: str


class DeviceOut(BaseModel):
    # NB: NESSUN campo segreto (api_key_enc/api_secret_enc) — write-only.
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    base_url: str
    verify_tls: bool
    tls_fingerprint: str | None
    site: str | None
    tags: list[str]
    status: str
    last_seen: datetime | None
    firmware_version: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TestResultOut(BaseModel):
    status: str  # reachable | unverified
    firmware_version: str | None = None
    error: str | None = None
