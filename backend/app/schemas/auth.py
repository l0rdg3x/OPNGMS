import uuid

from pydantic import BaseModel, EmailStr


class SetupIn(BaseModel):
    email: EmailStr
    name: str
    password: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class MeOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    name: str
    is_superadmin: bool
