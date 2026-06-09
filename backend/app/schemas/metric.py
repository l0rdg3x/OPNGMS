from datetime import datetime

from pydantic import BaseModel


class MetricPoint(BaseModel):
    time: datetime
    label: str
    value: float


class MetricSeriesOut(BaseModel):
    metric: str
    points: list[MetricPoint]
    last: list[MetricPoint]  # ultimo valore per label
