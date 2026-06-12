import uuid

import aiosmtplib
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import worker
from app.core.db import set_tenant_context
from app.models.smtp_settings import SINGLETON_ID, SmtpSettings
from tests.factories import make_user


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
        await make_user(s, email="sa@x.io", password="pw12345", is_superadmin=True)
        s.add(SmtpSettings(id=SINGLETON_ID, enabled=True, host="smtp.x.io", port=587,
                           security="starttls", username=None, password_enc=None,
                           from_email="noc@x.io", from_name="NOC"))
        await _seed_silent_tenant(s)
        await s.commit()

    async def fake_stats(settings, *, window_hours=24):
        return {}  # no logs -> the enabled tenant is silent
    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats)

    sent: dict = {}

    async def fake_send(message, **kwargs):
        sent["to"] = message["To"]
        sent["subject"] = message["Subject"]
        return ({}, "ok")
    monkeypatch.setattr(aiosmtplib, "send", fake_send)

    summary = await worker.detect_silent_tenants({"session_factory": factory})
    assert summary["new"] == 1 and summary["emailed"] is True
    assert sent["to"] == "sa@x.io"
    assert "Acme" in sent["subject"]


async def test_detect_silent_tenants_no_smtp_no_email(db_engine, monkeypatch):
    # SMTP disabled -> alert row still created (dashboard), but no email and emailed=False.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345", is_superadmin=True)
        await _seed_silent_tenant(s)
        await s.commit()

    async def fake_stats(settings, *, window_hours=24):
        return {}
    monkeypatch.setattr("app.services.silent_alerts.fleet_log_stats", fake_stats)

    called = {"send": False}

    async def fake_send(message, **kwargs):
        called["send"] = True
        return ({}, "ok")
    monkeypatch.setattr(aiosmtplib, "send", fake_send)

    summary = await worker.detect_silent_tenants({"session_factory": factory})
    assert summary["new"] == 1 and summary["emailed"] is False
    assert called["send"] is False
