import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LogForwardingOut(BaseModel):
    device_id: uuid.UUID
    enabled: bool
    cert_serial: str
    cert_fingerprint: str
    provisioned_at: datetime | None
    cert_not_after: datetime | None = None
    last_log_at: datetime | None = None
    revoked_at: datetime | None = None


class RevokeIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
