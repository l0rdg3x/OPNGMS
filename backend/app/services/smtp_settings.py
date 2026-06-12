"""Read/write the global SMTP singleton, encrypting the password at rest (Fernet)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.smtp_settings import SINGLETON_ID, SmtpSettings
from app.services.email.smtp import SmtpSendConfig


class SmtpSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SmtpSettings | None:
        return (await self.session.execute(select(SmtpSettings))).scalar_one_or_none()

    async def upsert(self, *, enabled: bool, host: str, port: int, security: str,
                     username: str | None, from_email: str, from_name: str,
                     password: str | None, clear_password: bool) -> SmtpSettings:
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
        await self.session.flush()
        return row

    def to_send_config(self, row: SmtpSettings) -> SmtpSendConfig:
        return SmtpSendConfig(
            host=row.host, port=row.port, security=row.security, username=row.username,
            password=crypto.decrypt(row.password_enc) if row.password_enc else None,
            from_email=row.from_email, from_name=row.from_name,
        )
