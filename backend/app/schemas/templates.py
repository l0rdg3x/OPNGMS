import uuid
from datetime import datetime

from pydantic import BaseModel


class TemplateIn(BaseModel):
    kind: str = "firewall_alias"
    name: str
    description: str = ""
    body: dict = {}


class TemplateUpdateIn(BaseModel):
    name: str | None = None
    description: str | None = None
    body: dict | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    kind: str
    name: str
    description: str
    body: dict
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OverrideIn(BaseModel):
    body_patch: dict = {}


class OverrideOut(BaseModel):
    id: uuid.UUID
    template_id: uuid.UUID
    body_patch: dict
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApplyTemplateIn(BaseModel):
    scheduled_at: datetime | None = None


class TemplatePreviewOut(BaseModel):
    operation: str
    kind: str
    target: str
    new: dict
