from pydantic import BaseModel


class PasswordIn(BaseModel):
    password: str


class CodeIn(BaseModel):
    code: str


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
