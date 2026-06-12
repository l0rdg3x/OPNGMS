from pydantic import BaseModel, EmailStr, Field

SECURITIES = {"starttls", "tls", "none"}


class SmtpSettingsIn(BaseModel):
    enabled: bool = False
    host: str = Field(max_length=255)
    port: int = Field(ge=1, le=65535)
    security: str = "starttls"
    username: str | None = Field(default=None, max_length=255)
    from_email: EmailStr
    from_name: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=1024)  # None or "" -> keep existing; use clear_password=True to wipe
    clear_password: bool = False


class SmtpSettingsOut(BaseModel):
    enabled: bool
    host: str
    port: int
    security: str
    username: str | None
    from_email: str
    from_name: str
    has_password: bool


class SmtpTestIn(SmtpSettingsIn):
    to: EmailStr


class SmtpTestOut(BaseModel):
    ok: bool
    detail: str = ""
