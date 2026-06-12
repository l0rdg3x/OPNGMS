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


class LogFleetDeviceRow(BaseModel):
    device_id: uuid.UUID
    name: str
    forwarding: str  # enabled | disabled | revoked | none
    last_log_at: datetime | None
    volume: int | None
    is_silent: bool


class LogFleetDevicesTotals(BaseModel):
    enabled_devices: int
    silent_devices: int
    volume: int


class LogFleetDevicesOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    devices: list[LogFleetDeviceRow]
    totals: LogFleetDevicesTotals
    window: str = "24h"


class SilentTenantAlertOut(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    silent_since: datetime

    model_config = {"from_attributes": True}
