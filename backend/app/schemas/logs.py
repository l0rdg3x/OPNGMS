import uuid
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class LogCursor(BaseModel):
    pit_id: str = Field(max_length=8192)
    after: list[Any]


class LogSearchIn(BaseModel):
    query: str = Field(default="", max_length=2048)
    device_id: uuid.UUID | None = None
    frm: AwareDatetime
    to: AwareDatetime
    size: int = Field(default=100, ge=1)
    cursor: LogCursor | None = None


class LogHitOut(BaseModel):
    id: str
    timestamp: str
    device_id: str
    host: str
    program: str
    message: str
    source: dict


class LogSearchOut(BaseModel):
    total: int
    hits: list[LogHitOut]
    next_cursor: LogCursor | None = None
