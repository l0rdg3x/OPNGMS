import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LogSearchIn(BaseModel):
    query: str = Field(default="", max_length=2048)
    device_id: uuid.UUID | None = None
    frm: datetime
    to: datetime
    page: int = Field(default=0, ge=0)
    size: int = Field(default=100, ge=1)


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
