import uuid
from datetime import datetime

from pydantic import BaseModel


class LogFleetRow(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    enabled: int
    disabled: int
    revoked: int
    total_devices: int
    last_log_at: datetime | None
    volume: int | None


class LogFleetTotals(BaseModel):
    tenants_with_forwarding: int
    enabled_devices: int
    volume: int
    silent_tenants: int


class LogFleetOut(BaseModel):
    tenants: list[LogFleetRow]
    totals: LogFleetTotals
    window: str = "24h"
