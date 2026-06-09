from datetime import datetime

from pydantic import BaseModel, Field


class ReportRequest(BaseModel):
    from_: datetime = Field(alias="from")
    to: datetime
    timezone: str = "UTC"

    model_config = {"populate_by_name": True}
