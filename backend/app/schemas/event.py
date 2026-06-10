import uuid
from datetime import datetime

from pydantic import BaseModel


class EventOut(BaseModel):
    time: datetime
    device_id: uuid.UUID
    source: str
    category: str
    src_ip: str
    dst_ip: str
    name: str
    severity: str
    action: str
    attributes: dict


class EventTopRow(BaseModel):
    value: str
    count: int


class EventPage(BaseModel):
    items: list[EventOut]
    next_cursor: str | None = None
