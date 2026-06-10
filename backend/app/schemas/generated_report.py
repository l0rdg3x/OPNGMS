import uuid
from datetime import datetime

from pydantic import BaseModel


class GeneratedReportOut(BaseModel):
    id: uuid.UUID
    kind: str
    period_from: datetime
    period_to: datetime
    created_by: uuid.UUID | None
    size: int
    created_at: datetime

    model_config = {"from_attributes": True}
