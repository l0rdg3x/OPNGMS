import uuid
from datetime import datetime

from pydantic import BaseModel


class ProfileIn(BaseModel):
    name: str
    description: str = ""
    template_ids: list[uuid.UUID] = []  # ordered member templates


class ProfileUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    template_ids: list[uuid.UUID] | None = None  # when present, replaces the ordered member set


class ProfileOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    version: int
    template_ids: list[uuid.UUID]
    created_at: datetime
    updated_at: datetime


class ApplyProfileIn(BaseModel):
    scheduled_at: datetime | None = None


class ProfileApplyOut(BaseModel):
    change_ids: list[uuid.UUID]
    status: str
