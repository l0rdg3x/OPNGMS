import uuid

from pydantic import BaseModel, EmailStr, Field


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str
    password: str = Field(min_length=12, max_length=1024)  # admin-created accounts: enforce a minimum
    is_superadmin: bool = False


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
    status: str

    model_config = {"from_attributes": True}
