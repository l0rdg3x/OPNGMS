import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DeviceIn(BaseModel):
    name: str
    base_url: str
    api_key: str
    api_secret: str
    verify_tls: bool = True
    tls_fingerprint: str | None = None
    site: str | None = None
    tags: list[str] = Field(default_factory=list)


class DeviceUpdateIn(BaseModel):
    name: str | None = None
    base_url: str | None = None
    verify_tls: bool | None = None
    tls_fingerprint: str | None = None
    site: str | None = None
    tags: list[str] | None = None


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
