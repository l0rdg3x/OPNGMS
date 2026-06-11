from pydantic import BaseModel, Field


class PasswordIn(BaseModel):
    password: str = Field(max_length=1024)


class CodeIn(BaseModel):
    code: str = Field(max_length=128)


class SetupOut(BaseModel):
    otpauth_uri: str
    secret: str


class RecoveryOut(BaseModel):
    recovery_codes: list[str]


class MfaStatusOut(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class MfaPolicyOut(BaseModel):
    mode: str


class MfaPolicyIn(BaseModel):
    mode: str
