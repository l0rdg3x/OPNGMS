import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.syslog_ca import SINGLETON_ID, SyslogCa
from app.models.syslog_ca_key import SyslogCaKey


async def test_syslog_ca_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # The cert lives in syslog_ca; the encrypted key in the owner-only syslog_ca_key (same id).
        s.add(SyslogCa(id=SINGLETON_ID, cert_pem="-----CA-----"))
        s.add(SyslogCaKey(id=SINGLETON_ID, key_enc=b"enc"))
        await s.commit()
        row = (await s.execute(select(SyslogCa))).scalar_one()
        assert row.id == SINGLETON_ID
        key = (await s.execute(select(SyslogCaKey))).scalar_one()
        assert key.id == SINGLETON_ID and key.key_enc == b"enc"


async def test_device_log_forwarding_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        s.add(DeviceLogForwarding(device_id=did, tenant_id=tid, enabled=True, cert_serial="01",
                                  cert_fingerprint="ab", opnsense_cert_uuid="u1", opnsense_dest_uuid="u2"))
        await s.commit()
        row = (await s.execute(select(DeviceLogForwarding))).scalar_one()
        assert row.enabled is True and row.opnsense_dest_uuid == "u2"
