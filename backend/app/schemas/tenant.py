import uuid

from pydantic import BaseModel


class TenantIn(BaseModel):
    name: str
    slug: str
    note: str | None = None


class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    note: str | None

    model_config = {"from_attributes": True}
