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
    auth_method: str = "password"
    oauth_provider: str | None = Field(default=None, max_length=32)
    oauth_client_id: str | None = Field(default=None, max_length=512)
    oauth_client_secret: str | None = Field(default=None, max_length=2048)
    oauth_refresh_token: str | None = Field(default=None, max_length=4096)
    oauth_tenant_id: str | None = Field(default=None, max_length=128)
    clear_client_secret: bool = False
    clear_refresh_token: bool = False


class SmtpSettingsOut(BaseModel):
    enabled: bool
    host: str
    port: int
    security: str
    username: str | None
    from_email: str
    from_name: str
    has_password: bool
    auth_method: str
    oauth_provider: str | None
    oauth_client_id: str | None
    oauth_tenant_id: str | None
    has_client_secret: bool
    has_refresh_token: bool


class SmtpTestIn(SmtpSettingsIn):
    to: EmailStr


class SmtpTestOut(BaseModel):
    ok: bool
    detail: str = ""
