import uuid
from datetime import datetime

from pydantic import BaseModel


class LogForwardingOut(BaseModel):
    device_id: uuid.UUID
    enabled: bool
    cert_serial: str
    cert_fingerprint: str
    provisioned_at: datetime | None
