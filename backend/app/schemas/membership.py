import uuid
from typing import Literal

from pydantic import BaseModel


class MembershipIn(BaseModel):
    user_id: uuid.UUID
    role: Literal["tenant_admin", "operator", "read_only"]


class MembershipOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    role: str

    model_config = {"from_attributes": True}
