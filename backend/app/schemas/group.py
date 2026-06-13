import uuid

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.rbac import TENANT_ROLES


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""


class GroupUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class GroupGrantIn(BaseModel):
    all_tenants: bool = False
    tenant_id: uuid.UUID | None = None
    role: str

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        # A group grant can ONLY carry a tenant role — never an org/superadmin capability.
        if v not in TENANT_ROLES:
            raise ValueError(f"role must be one of {sorted(TENANT_ROLES)}")
        return v

    @model_validator(mode="after")
    def _scope(self) -> GroupGrantIn:
        # Exactly one of: wildcard (all tenants) OR a specific tenant.
        if self.all_tenants == (self.tenant_id is not None):
            raise ValueError("set exactly one of all_tenants=true or tenant_id")
        return self


class GroupGrantOut(BaseModel):
    id: uuid.UUID
    all_tenants: bool
    tenant_id: uuid.UUID | None
    role: str

    model_config = {"from_attributes": True}


class GroupMembersIn(BaseModel):
    user_ids: list[uuid.UUID]


class GroupOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    member_ids: list[uuid.UUID]
    grants: list[GroupGrantOut]
