from pydantic import BaseModel


class LivePushIn(BaseModel):
    enabled: bool


class LivePushOut(BaseModel):
    enabled: bool
