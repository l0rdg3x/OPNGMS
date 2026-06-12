import uuid

from pydantic import AwareDatetime, BaseModel, Field


class LogSearchIn(BaseModel):
    query: str = Field(default="", max_length=2048)
    device_id: uuid.UUID | None = None
    # Require timezone-aware bounds: a naive datetime would make the frm/to
    # comparison raise TypeError (-> 500) and would be sent to OpenSearch
    # without an offset, silently interpreted as the host's local time.
    frm: AwareDatetime
    to: AwareDatetime
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
