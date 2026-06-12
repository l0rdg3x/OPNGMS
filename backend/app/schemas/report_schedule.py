import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReportScheduleIn(BaseModel):
    device_id: uuid.UUID | None = None
    enabled: bool = True
    frequency: str  # weekly | monthly | on_demand
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int = Field(default=4, ge=0, le=23)
    recipients: list[str] = Field(default_factory=list, max_length=200)


class ReportScheduleOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    enabled: bool
    frequency: str
    weekday: int | None
    hour: int
    recipients: list[str]
    next_run_at: datetime | None
    last_run_at: datetime | None
