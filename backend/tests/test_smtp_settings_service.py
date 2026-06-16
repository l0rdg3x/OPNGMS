from unittest.mock import AsyncMock, patch

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
        cfg = await svc.resolve_send_config(row)
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
        assert (await svc.resolve_send_config(row)).password == "keepme"  # preserved
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


async def test_oauth_upsert_encrypts_secrets_and_keeps_on_blank(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        row = await svc.upsert(
            enabled=True, host="smtp.gmail.com", port=587, security="starttls", username=None,
            from_email="me@x.com", from_name="Me", password=None, clear_password=False,
            auth_method="oauth", oauth_provider="google", oauth_client_id="cid",
            oauth_client_secret="secret", oauth_refresh_token="refresh", oauth_tenant_id=None,
            clear_client_secret=False, clear_refresh_token=False,
        )
        await s.commit()
        assert row.auth_method == "oauth" and row.oauth_client_secret_enc and row.oauth_refresh_token_enc
        assert b"secret" not in row.oauth_client_secret_enc  # encrypted
        assert b"refresh" not in row.oauth_refresh_token_enc  # encrypted
        enc1 = row.oauth_refresh_token_enc
        # Blank secret + not-clear -> keep existing.
        row = await svc.upsert(
            enabled=True, host="smtp.gmail.com", port=587, security="starttls", username=None,
            from_email="me@x.com", from_name="Me", password=None, clear_password=False,
            auth_method="oauth", oauth_provider="google", oauth_client_id="cid",
            oauth_client_secret=None, oauth_refresh_token=None, oauth_tenant_id=None,
            clear_client_secret=False, clear_refresh_token=False,
        )
        await s.commit()
        assert row.oauth_refresh_token_enc == enc1


async def test_resolve_send_config_oauth_fetches_access_token(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        row = await svc.upsert(
            enabled=True, host="smtp.gmail.com", port=587, security="starttls", username=None,
            from_email="me@x.com", from_name="Me", password=None, clear_password=False,
            auth_method="oauth", oauth_provider="google", oauth_client_id="cid",
            oauth_client_secret="secret", oauth_refresh_token="refresh", oauth_tenant_id=None,
            clear_client_secret=False, clear_refresh_token=False,
        )
        await s.commit()
        with patch("app.services.smtp_settings.fetch_access_token",
                   AsyncMock(return_value="ya29.tok")) as m:
            cfg = await svc.resolve_send_config(row)
        assert cfg.access_token == "ya29.tok" and cfg.password is None and cfg.username == "me@x.com"
        m.assert_awaited_once_with("google", "cid", "secret", "refresh", "")


async def test_resolve_send_config_password_unchanged(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        row = await svc.upsert(
            enabled=True, host="smtp.x.com", port=587, security="starttls", username="u",
            from_email="me@x.com", from_name="Me", password="pw", clear_password=False,
            auth_method="password", oauth_provider=None, oauth_client_id=None,
            oauth_client_secret=None, oauth_refresh_token=None, oauth_tenant_id=None,
            clear_client_secret=False, clear_refresh_token=False,
        )
        await s.commit()
        cfg = await svc.resolve_send_config(row)
        assert cfg.password == "pw" and cfg.access_token is None
