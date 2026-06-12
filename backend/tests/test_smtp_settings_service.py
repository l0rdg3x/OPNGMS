from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.smtp_settings import SmtpSettingsService


async def test_upsert_encrypts_password_and_to_send_config(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        row = await svc.upsert(enabled=True, host="smtp.x.io", port=587, security="starttls",
                               username="u", from_email="noc@x.io", from_name="NOC",
                               password="secret", clear_password=False)
        await s.commit()
        assert row.password_enc is not None
        assert b"secret" not in row.password_enc  # encrypted, not plaintext
        cfg = svc.to_send_config(row)
        assert cfg.password == "secret"
        assert cfg.host == "smtp.x.io"


async def test_upsert_keeps_password_when_omitted(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        await svc.upsert(enabled=True, host="h", port=587, security="starttls", username="u",
                         from_email="n@x.io", from_name="N", password="keepme", clear_password=False)
        await s.commit()
        row = await svc.upsert(enabled=True, host="h2", port=25, security="none", username="u",
                               from_email="n@x.io", from_name="N", password=None, clear_password=False)
        await s.commit()
        assert svc.to_send_config(row).password == "keepme"  # preserved
        assert row.host == "h2"


async def test_clear_password(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        await svc.upsert(enabled=True, host="h", port=587, security="starttls", username="u",
                         from_email="n@x.io", from_name="N", password="x", clear_password=False)
        await s.commit()
        row = await svc.upsert(enabled=True, host="h", port=587, security="none", username=None,
                               from_email="n@x.io", from_name="N", password=None, clear_password=True)
        await s.commit()
        assert row.password_enc is None
