import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.syslog_ca import SyslogCa
from app.models.syslog_ca_key import SyslogCaKey
from app.services.log_forwarding import SyslogCaService, provision_device
from tests.factories import seed_syslog_ca


class FakeClient:
    def __init__(self):
        self.calls = []

    async def import_ca(self, pem, *, descr):
        self.calls.append("import_ca"); return "ca-uuid"

    async def import_cert(self, cert, key, *, descr):
        self.calls.append("import_cert"); return "cert-uuid"

    async def add_syslog_destination(self, *, hostname, port, certificate_uuid, description="x"):
        self.calls.append(("dest", hostname, port, certificate_uuid)); return "dest-uuid"


async def test_ensure_ca_is_idempotent(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SyslogCaService(s)
        a = await svc.ensure_ca(); await s.commit()
        b = await svc.ensure_ca(); await s.commit()
        assert a.cert_pem == b.cert_pem
        assert len((await s.execute(select(SyslogCa))).scalars().all()) == 1
        # ensure_ca writes BOTH the cert row and the owner-only key row (one each).
        assert len((await s.execute(select(SyslogCaKey))).scalars().all()) == 1


async def test_provision_device_issues_imports_and_configures(db_engine):
    await seed_syslog_ca(db_engine)  # CA must exist owner-side; provisioning no longer creates it
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        client = FakeClient()
        row = await provision_device(s, tenant_id=tid, device_id=did, client=client,
                                     receiver_host="logs.example", receiver_port=6514)
        await s.commit()
        assert row.enabled is True
        assert row.opnsense_dest_uuid == "dest-uuid"
        assert client.calls[0] == "import_ca" and client.calls[1] == "import_cert"
        assert (await s.get(DeviceLogForwarding, did)).cert_serial != ""
