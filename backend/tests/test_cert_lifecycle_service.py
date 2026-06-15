import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.revoked_syslog_cert import RevokedSyslogCert
from app.services.log_forwarding import revoke_device, rotate_device_cert
from tests.factories import seed_syslog_ca


class StubClient:
    """Records box calls; returns predictable new UUIDs."""
    def __init__(self):
        self.calls = []

    async def import_cert(self, cert_pem, key_pem, *, descr):
        self.calls.append(("import_cert", descr)); return "newcert-uuid"

    async def add_syslog_destination(self, *, hostname, port, certificate_uuid):
        self.calls.append(("add_dest", certificate_uuid)); return "newdest-uuid"

    async def delete_syslog_destination(self, dest_uuid):
        self.calls.append(("del_dest", dest_uuid)); return {}

    async def delete_cert(self, cert_uuid):
        self.calls.append(("del_cert", cert_uuid)); return {}


async def _seed_enabled(db_engine):
    await seed_syslog_ca(db_engine)  # rotate_device_cert requires the CA to exist owner-side
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding "
            "(device_id,tenant_id,enabled,cert_serial,cert_fingerprint,opnsense_cert_uuid,opnsense_dest_uuid) "
            "VALUES (:d,:t,true,'oldserial','oldfp','oldcert','olddest')"), {"d": did, "t": tid})
        await s.commit()
    return tid, did


async def test_rotate_swaps_cert_and_updates_row(db_engine):
    tid, did = await _seed_enabled(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    client = StubClient()
    async with factory() as s:
        await set_tenant_context(s, tid)
        row = await rotate_device_cert(s, tenant_id=tid, device_id=did, client=client,
                                       receiver_host="logs.example", receiver_port=6514)
        await s.commit()
    names = [c[0] for c in client.calls]
    assert names.index("add_dest") < names.index("del_dest")
    assert ("del_dest", "olddest") in client.calls and ("del_cert", "oldcert") in client.calls
    assert row.opnsense_cert_uuid == "newcert-uuid" and row.opnsense_dest_uuid == "newdest-uuid"
    assert row.cert_serial != "oldserial" and row.enabled is True


async def test_revoke_records_serial_and_disables(db_engine):
    tid, did = await _seed_enabled(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    client = StubClient()
    async with factory() as s:
        await set_tenant_context(s, tid)
        row = await revoke_device(s, tenant_id=tid, device_id=did, client=client, reason="key leak")
        await s.commit()
    assert row.enabled is False and row.revoked_at is not None
    async with factory() as s:
        await set_tenant_context(s, tid)
        led = (await s.execute(select(RevokedSyslogCert))).scalars().all()
    assert len(led) == 1 and led[0].serial == "oldserial" and led[0].reason == "key leak"


async def test_rotate_rejects_disabled_device(db_engine):
    tid, did = await _seed_enabled(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await set_tenant_context(s, tid)
        await s.execute(text("UPDATE device_log_forwarding SET enabled=false WHERE device_id=:d"), {"d": did})
        await s.commit()
    async with factory() as s:
        await set_tenant_context(s, tid)
        with pytest.raises(ValueError):
            await rotate_device_cert(s, tenant_id=tid, device_id=did, client=StubClient(),
                                     receiver_host="h", receiver_port=6514)
