from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.smtp_settings import SINGLETON_ID, SmtpSettings


async def test_smtp_settings_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        s.add(SmtpSettings(
            id=SINGLETON_ID, enabled=True, host="smtp.x.io", port=587, security="starttls",
            username="u", password_enc=b"enc", from_email="noc@x.io", from_name="NOC",
        ))
        await s.commit()
    async with factory() as s:
        row = (await s.execute(select(SmtpSettings))).scalar_one()
        assert row.id == SINGLETON_ID
        assert row.host == "smtp.x.io"
        assert row.enabled is True
        assert row.password_enc == b"enc"
