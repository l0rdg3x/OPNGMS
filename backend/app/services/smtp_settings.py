"""Read/write the global SMTP singleton, encrypting secrets at rest (Fernet).

For OAuth auth, `resolve_send_config` exchanges the stored refresh token for a short-lived access
token at send time (never persisted, never logged).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.smtp_settings import SINGLETON_ID, SmtpSettings
from app.services.email.oauth import fetch_access_token
from app.services.email.smtp import SmtpSendConfig


class SmtpSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SmtpSettings | None:
        return (await self.session.execute(select(SmtpSettings))).scalar_one_or_none()

    async def upsert(self, *, enabled: bool, host: str, port: int, security: str,
                     username: str | None, from_email: str, from_name: str,
                     password: str | None, clear_password: bool,
                     auth_method: str = "password", oauth_provider: str | None = None,
                     oauth_client_id: str | None = None, oauth_client_secret: str | None = None,
                     oauth_refresh_token: str | None = None, oauth_tenant_id: str | None = None,
                     clear_client_secret: bool = False,
                     clear_refresh_token: bool = False) -> SmtpSettings:
        row = await self.get()
        if row is None:
            row = SmtpSettings(id=SINGLETON_ID)
            self.session.add(row)
        row.enabled, row.host, row.port, row.security = enabled, host, port, security
        row.username = username or None
        row.from_email, row.from_name = from_email, from_name
        if clear_password:
            row.password_enc = None
        elif password:
            row.password_enc = crypto.encrypt(password)
        # password is None and not clear_password -> keep existing
        row.auth_method = auth_method
        row.oauth_provider = oauth_provider or None
        row.oauth_client_id = oauth_client_id or None
        row.oauth_tenant_id = oauth_tenant_id or None
        if clear_client_secret:
            row.oauth_client_secret_enc = None
        elif oauth_client_secret:
            row.oauth_client_secret_enc = crypto.encrypt(oauth_client_secret)
        if clear_refresh_token:
            row.oauth_refresh_token_enc = None
        elif oauth_refresh_token:
            row.oauth_refresh_token_enc = crypto.encrypt(oauth_refresh_token)
        await self.session.flush()
        return row

    async def store_oauth_refresh_token(self, provider: str, refresh_token: str) -> SmtpSettings:
        """Persist a refresh token obtained via the OAuth Connect flow onto the existing singleton
        (client id+secret must already be saved). Sets auth_method=oauth + the provider."""
        row = await self.get()
        if row is None:
            row = SmtpSettings(id=SINGLETON_ID)
            self.session.add(row)
        row.auth_method = "oauth"
        row.oauth_provider = provider
        row.oauth_refresh_token_enc = crypto.encrypt(refresh_token)
        await self.session.flush()
        return row

    async def resolve_send_config(self, row: SmtpSettings) -> SmtpSendConfig:
        if row.auth_method == "oauth":
            token = await fetch_access_token(
                row.oauth_provider or "", row.oauth_client_id or "",
                crypto.decrypt(row.oauth_client_secret_enc) if row.oauth_client_secret_enc else "",
                crypto.decrypt(row.oauth_refresh_token_enc) if row.oauth_refresh_token_enc else "",
                row.oauth_tenant_id or "",
            )
            return SmtpSendConfig(
                host=row.host, port=row.port, security=row.security,
                username=row.from_email, password=None, access_token=token,
                from_email=row.from_email, from_name=row.from_name,
            )
        return SmtpSendConfig(
            host=row.host, port=row.port, security=row.security, username=row.username,
            password=crypto.decrypt(row.password_enc) if row.password_enc else None,
            from_email=row.from_email, from_name=row.from_name,
        )
