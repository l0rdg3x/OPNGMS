import uuid
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import worker
from app.core.db import set_tenant_context
from app.models.smtp_settings import SINGLETON_ID, SmtpSettings
from tests.factories import make_user


def _mock_smtp_client(captured: dict):
    """A mock aiosmtplib.SMTP client recording the sent message; install via monkeypatch on
    app.services.email.smtp.aiosmtplib.SMTP."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.auth_xoauth2 = AsyncMock()
    client.login = AsyncMock()

    async def fake_send_message(message):
        captured["to"] = message["To"]
        captured["subject"] = message["Subject"]
        return ({}, "ok")

    client.send_message = AsyncMock(side_effect=fake_send_message)
    return client


async def _seed_silent_tenant(s, *, name="Acme", slug="acme"):
    tid, did = uuid.uuid4(), uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
                    {"i": tid, "n": name, "sl": slug})
    await set_tenant_context(s, tid)
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
    await s.execute(text(
        "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
        "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
    return tid


async def test_detect_silent_tenants_emails_superadmins(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        s.add(SmtpSettings(id=SINGLETON_ID, enabled=True, host="smtp.x.io", port=587,
                           security="starttls", username=None, password_enc=None,
                           from_email="noc@x.io", from_name="NOC"))
        await _seed_silent_tenant(s)
        await s.commit()

    async def fake_stats(settings, *, window_hours=24):
        return {}  # no logs -> the enabled tenant is silent
    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats)

    sent: dict = {}
    client = _mock_smtp_client(sent)
    monkeypatch.setattr("app.services.email.smtp.aiosmtplib.SMTP", lambda **kw: client)

    summary = await worker.detect_silent_tenants({"session_factory": factory})
    assert summary["new"] == 1 and summary["emailed"] is True
    assert sent["to"] == "sa@x.io"
    assert "Acme" in sent["subject"]


async def test_detect_silent_tenants_no_smtp_no_email(db_engine, monkeypatch):
    # SMTP disabled -> alert row still created (dashboard), but no email and emailed=False.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345-secure", is_superadmin=True)
        await _seed_silent_tenant(s)
        await s.commit()

    async def fake_stats(settings, *, window_hours=24):
        return {}
    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats)

    called = {"send": False}

    def _boom(**kw):
        called["send"] = True
        raise AssertionError("SMTP client must not be constructed when SMTP is disabled")
    monkeypatch.setattr("app.services.email.smtp.aiosmtplib.SMTP", _boom)

    summary = await worker.detect_silent_tenants({"session_factory": factory})
    assert summary["new"] == 1 and summary["emailed"] is False
    assert called["send"] is False
