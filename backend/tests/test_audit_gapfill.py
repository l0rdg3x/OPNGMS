import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.audit import AuditLog
from tests.factories import make_membership, make_tenant, make_user


@pytest.fixture
def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _login(api_client, session_factory, *, email="sa@test.com"):
    """Create a superadmin + a tenant with a device, log the client in, return (tenant_id, device_id)."""
    async with session_factory() as s:
        user = await make_user(s, email=email, password="pw12345-secure", is_superadmin=True)
        tenant = await make_tenant(s, slug=email.split("@")[0])
        await make_membership(s, user_id=user.id, tenant_id=tenant.id, role="tenant_admin")
        await s.commit()
        tenant_id = tenant.id
    # seed a device for that tenant (owner session, set RLS context)
    device_id = uuid.uuid4()
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        await s.execute(
            sa.text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                "verify_tls, status, tags) VALUES (:id,:t,'fw','https://fw',''::bytea,''::bytea,"
                "true,'unverified','{}')"
            ),
            {"id": device_id, "t": tenant_id},
        )
        await s.commit()
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    assert r.status_code == 200
    return tenant_id, device_id


async def test_firmware_action_writes_audit(api_client, session_factory):
    tenant_id, device_id = await _login(api_client, session_factory)
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/devices/{device_id}/firmware/action",
        json={"kind": "firmware_update"},
        headers={"X-OPNGMS-CSRF": csrf},
    )
    assert r.status_code == 201
    async with session_factory() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action == "device.firmware.action"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_id == str(device_id)
    assert rows[0].details.get("kind") == "firmware_update"


async def test_setup_writes_audit(api_client, session_factory):
    r = await api_client.post(
        "/api/setup",
        json={"email": "first@admin.io", "name": "First", "password": "pw12345-secure"},
    )
    assert r.status_code == 201
    async with session_factory() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action == "setup.bootstrap"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id is None
    assert rows[0].details.get("email") == "first@admin.io"


async def test_send_now_writes_audit(api_client, session_factory):
    tenant_id, _ = await _login(api_client, session_factory, email="sa2@test.com")
    sched_id = uuid.uuid4()
    async with session_factory() as s:
        await set_tenant_context(s, tenant_id)
        await s.execute(
            sa.text(
                "INSERT INTO report_schedule "
                "(id, tenant_id, frequency, recipients, enabled, hour) "
                "VALUES (:id,:t,'weekly','{}',true,4)"
            ),
            {"id": sched_id, "t": tenant_id},
        )
        await s.commit()
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post(
        f"/api/tenants/{tenant_id}/report-schedules/{sched_id}/send-now",
        headers={"X-OPNGMS-CSRF": csrf},
    )
    assert r.status_code == 202
    async with session_factory() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action == "report.schedule.send_now"))
        ).scalars().all()
    assert len(rows) == 1 and rows[0].target_id == str(sched_id)


async def test_mfa_setup_writes_audit(api_client, session_factory):
    email = "mfauser@test.com"
    async with session_factory() as s:
        user = await make_user(s, email=email, password="pw12345-secure")
        await s.commit()
        user_id = user.id
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    assert r.status_code == 200
    csrf = api_client.cookies.get("opngms_csrf")
    r = await api_client.post(
        "/api/me/mfa/setup",
        json={"password": "pw12345-secure"},
        headers={"X-OPNGMS-CSRF": csrf},
    )
    assert r.status_code == 200
    async with session_factory() as s:
        rows = (
            await s.execute(select(AuditLog).where(AuditLog.action == "mfa.setup_start"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].tenant_id is None
    assert rows[0].target_id == str(user_id)
