import uuid
from datetime import datetime

from pydantic import BaseModel


class AlertOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    type: str
    label: str
    severity: str
    opened_at: datetime
    resolved_at: datetime | None
    details: dict

    model_config = {"from_attributes": True}
