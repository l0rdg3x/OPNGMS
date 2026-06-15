import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.services.cert_renewal import due_for_renewal, renew_expiring_device_certs
from tests.factories import seed_syslog_ca


class _S:
    syslog_receiver_host = "logs.example"
    syslog_tls_port = 6514
    cert_renewal_window_days = 30


def test_due_for_renewal():
    now = datetime(2026, 6, 1, tzinfo=UTC)
    w = timedelta(days=30)
    assert due_for_renewal(None, now=now, window=w) is False
    assert due_for_renewal(now + timedelta(days=60), now=now, window=w) is False
    assert due_for_renewal(now + timedelta(days=10), now=now, window=w) is True
    assert due_for_renewal(now - timedelta(days=1), now=now, window=w) is True


class _StubClient:
    def __init__(self, *, fail=False):
        self.fail = fail
    # rotate_device_cert calls these:
    async def import_cert(self, *a, **k):
        if self.fail:
            from app.connectors.opnsense.client import OpnsenseError
            raise OpnsenseError("boom")
        return "newcert"
    async def add_syslog_destination(self, **k): return "newdest"
    async def delete_syslog_destination(self, u): return {}
    async def delete_cert(self, u): return {}


async def _seed(db_engine, *, cert_not_after, enabled=True):
    await seed_syslog_ca(db_engine)  # rotate_device_cert requires the CA to exist
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    slug = tid.hex[:12]  # unique per seeded tenant (tenants.slug is UNIQUE)
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sg,'active')"),
                        {"i": tid, "n": slug, "sg": slug})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,false,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint,cert_not_after,opnsense_cert_uuid,opnsense_dest_uuid) "
            "VALUES (:d,:t,:e,'old','oldfp',:na,'oldcert','olddest')"),
            {"d": did, "t": tid, "e": enabled, "na": cert_not_after})
        await s.commit()
    return tid, did


async def test_renews_only_expiring(db_engine):
    soon = datetime.now(UTC) + timedelta(days=10)
    far = datetime.now(UTC) + timedelta(days=200)
    tid_s, did_s = await _seed(db_engine, cert_not_after=soon)
    tid_f, did_f = await _seed(db_engine, cert_not_after=far)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        res = await renew_expiring_device_certs(s, _S(), client_for=lambda d: _StubClient())
        await s.commit()
    assert res["renewed"] == 1 and res["considered"] >= 1
    # the soon row got a new serial; the far row is unchanged
    async with factory() as s:
        ser_s = (await s.execute(text("SELECT cert_serial FROM device_log_forwarding WHERE device_id=:d"), {"d": did_s})).scalar_one()
        ser_f = (await s.execute(text("SELECT cert_serial FROM device_log_forwarding WHERE device_id=:d"), {"d": did_f})).scalar_one()
    assert ser_s != "old" and ser_f == "old"


async def test_box_failure_counts_and_continues(db_engine):
    soon = datetime.now(UTC) + timedelta(days=5)
    await _seed(db_engine, cert_not_after=soon)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        res = await renew_expiring_device_certs(s, _S(), client_for=lambda d: _StubClient(fail=True))
    assert res["failed"] == 1 and res["renewed"] == 0
