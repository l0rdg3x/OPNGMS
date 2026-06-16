import pyotp
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core import crypto
from app.models.trusted_device import TrustedDevice
from app.models.user_mfa import UserMfa
from app.services import mfa as mfa_svc
from app.services.trusted_device import TrustedDeviceService
from tests.conftest import csrf_headers
from tests.factories import make_user


async def _login_full(api_client, db_engine, email="mgr@x.io"):
    """Seed a TOTP user, log in, clear the challenge with a code -> full session in the client jar."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email=email, password="pw12345-secure")
        secret = mfa_svc.new_secret()
        s.add(UserMfa(user_id=u.id, enabled=True, totp_secret_enc=crypto.encrypt(secret)))
        await s.commit()
        uid = u.id
    await api_client.post("/api/login", json={"email": email, "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    await api_client.post("/api/login/mfa", json={"code": pyotp.TOTP(secret).now()}, headers=h)
    return uid


async def test_list_and_delete_one(api_client, db_engine):
    uid = await _login_full(api_client, db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        r1, _ = await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="A", ip=None)
        r2, _ = await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="B", ip=None)
        await s.commit()
        id1 = r1.id
    resp = await api_client.get("/api/me/trusted-devices")
    assert resp.status_code == 200
    assert len(resp.json()) == 2
    h = csrf_headers(api_client)
    d = await api_client.request("DELETE", f"/api/me/trusted-devices/{id1}", headers=h)
    assert d.status_code == 204
    assert len((await api_client.get("/api/me/trusted-devices")).json()) == 1


async def test_delete_all(api_client, db_engine):
    uid = await _login_full(api_client, db_engine, email="all@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="A", ip=None)
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent="B", ip=None)
        await s.commit()
    h = csrf_headers(api_client)
    d = await api_client.request("DELETE", "/api/me/trusted-devices", headers=h)
    assert d.status_code == 204
    assert (await api_client.get("/api/me/trusted-devices")).json() == []


async def test_delete_other_users_device_404(api_client, db_engine):
    await _login_full(api_client, db_engine, email="me2@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_user(s, email="other2@x.io", password="pw12345-secure")
        row, _ = await TrustedDeviceService(s).create_for_user(other.id, days=30, user_agent=None, ip=None)
        await s.commit()
        other_id = row.id
    h = csrf_headers(api_client)
    d = await api_client.request("DELETE", f"/api/me/trusted-devices/{other_id}", headers=h)
    assert d.status_code == 404


async def test_disable_mfa_revokes_trusted_devices(api_client, db_engine):
    uid = await _login_full(api_client, db_engine, email="dis@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
    h = csrf_headers(api_client)
    r = await api_client.post("/api/me/mfa/disable", json={"password": "pw12345-secure"}, headers=h)
    assert r.status_code == 204
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None


async def test_logout_all_revokes_trusted_devices(api_client, db_engine):
    uid = await _login_full(api_client, db_engine, email="loa@x.io")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await TrustedDeviceService(s).create_for_user(uid, days=30, user_agent=None, ip=None)
        await s.commit()
    h = csrf_headers(api_client)
    r = await api_client.post("/api/logout-all", headers=h)
    assert r.status_code == 204
    async with factory() as s:
        assert (await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == uid))).first() is None


async def test_mfa_admin_reset_revokes_trusted_devices(api_client, db_engine):
    # A superadmin resetting a user's MFA must also drop that user's trusted devices, or a prior
    # trusted cookie would bypass the fresh enrollment the reset mandates.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        target = await make_user(s, email="rtarget@x.io", password="pw12345-secure")
        await TrustedDeviceService(s).create_for_user(target.id, days=30, user_agent=None, ip=None)
        await make_user(s, email="radmin@x.io", password="pw12345-secure", is_superadmin=True)
        await s.commit()
        target_id = target.id
    # log in as the superadmin (no MFA -> full session) and reset the target's MFA
    await api_client.post("/api/login", json={"email": "radmin@x.io", "password": "pw12345-secure"})
    h = csrf_headers(api_client)
    r = await api_client.post(f"/api/users/{target_id}/mfa/reset", headers=h)
    assert r.status_code == 204, r.text
    async with factory() as s:
        assert (
            await s.execute(select(TrustedDevice).where(TrustedDevice.user_id == target_id))
        ).first() is None


async def test_purge_expired_helper(db_engine):
    from datetime import UTC, datetime, timedelta

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        u = await make_user(s, email="purge@x.io", password="pw12345-secure")
        svc = TrustedDeviceService(s)
        live, _ = await svc.create_for_user(u.id, days=30, user_agent=None, ip=None)
        dead, _ = await svc.create_for_user(u.id, days=30, user_agent=None, ip=None)
        dead.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
        assert await svc.purge_expired(datetime.now(UTC)) == 1
