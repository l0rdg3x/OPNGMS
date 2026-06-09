import uuid

from pydantic import BaseModel, EmailStr


class UserCreateIn(BaseModel):
    email: EmailStr
    name: str
    password: str
    is_superadmin: bool = False


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
    status: str

    model_config = {"from_attributes": True}
