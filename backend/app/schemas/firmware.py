import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

_KINDS = {"firmware_update", "firmware_upgrade", "plugin_install", "plugin_remove"}


class FirmwareActionIn(BaseModel):
    kind: str
    target: str = ""
    scheduled_at: datetime | None = None

    @field_validator("kind")
    @classmethod
    def _kind(cls, v: str) -> str:
        if v not in _KINDS:
            raise ValueError(f"invalid kind: {v}")
        return v


class FirmwareActionOut(BaseModel):
    id: uuid.UUID
    kind: str
    target: str
    status: str
    scheduled_at: datetime | None
    applied_at: datetime | None
    result: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class FirmwareCheckOut(BaseModel):
    status: str
    updates: int
    download_size: str
    needs_reboot: bool
    new_major: bool
