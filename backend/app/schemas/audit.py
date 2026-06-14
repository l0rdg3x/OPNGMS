import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AuditEntryOut(BaseModel):
    id: uuid.UUID
    ts: datetime
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    tenant_id: uuid.UUID | None
    tenant_name: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip: str | None
    details: dict

    model_config = ConfigDict(from_attributes=True)


class AuditListOut(BaseModel):
    items: list[AuditEntryOut]
    total: int
