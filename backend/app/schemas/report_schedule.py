import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, field_validator

from app.services.report_schedule import MAX_RECIPIENTS
from app.services.reporting.sections import SECTION_KEYS


class ReportScheduleIn(BaseModel):
    device_id: uuid.UUID | None = None
    enabled: bool = True
    frequency: str  # weekly | monthly | on_demand
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int = Field(default=4, ge=0, le=23)
    recipients: list[Annotated[str, Field(max_length=320)]] = Field(default_factory=list, max_length=MAX_RECIPIENTS)
    sections: dict[str, bool] | None = None  # None => inherit the tenant default

    @field_validator("sections")
    @classmethod
    def _sections(cls, v: dict[str, bool] | None) -> dict[str, bool] | None:
        if v is None:
            return None
        # Drop unknown section keys (forward-compat / stale-map safe); coerce to bool.
        return {k: bool(val) for k, val in v.items() if k in SECTION_KEYS}


class ReportScheduleOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    enabled: bool
    frequency: str
    weekday: int | None
    hour: int
    recipients: list[str]
    sections: dict[str, bool] | None
    next_run_at: datetime | None
    last_run_at: datetime | None
