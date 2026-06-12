"""DB-backed CA (key encrypted at rest) + per-device provisioning orchestration."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.syslog_ca import SINGLETON_ID, SyslogCa
from app.services.syslog_ca import build_ca, cert_serial_and_fingerprint, issue_device_cert


class SyslogCaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SyslogCa | None:
        return (await self.session.execute(select(SyslogCa))).scalar_one_or_none()

    async def ensure_ca(self) -> SyslogCa:
        row = await self.get()
        if row is not None:
            return row
        cert_pem, key_pem = build_ca()
        row = SyslogCa(id=SINGLETON_ID, cert_pem=cert_pem.decode(), key_enc=crypto.encrypt_bytes(key_pem))
        self.session.add(row)
        await self.session.flush()
        return row

    def device_cert(self, ca: SyslogCa, *, tenant_id: uuid.UUID, device_id: uuid.UUID) -> tuple[bytes, bytes]:
        return issue_device_cert(ca.cert_pem.encode(), crypto.decrypt_bytes(bytes(ca.key_enc)),
                                 tenant_id=str(tenant_id), device_id=str(device_id))


async def provision_device(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                           client, receiver_host: str, receiver_port: int) -> DeviceLogForwarding:
    """Issue a device cert, import the CA + cert into the box, configure the mTLS syslog destination,
    and record state. `client` is an OpnsenseClient (or a stub with the same methods)."""
    svc = SyslogCaService(session)
    ca = await svc.ensure_ca()
    cert_pem, key_pem = svc.device_cert(ca, tenant_id=tenant_id, device_id=device_id)
    serial, fp = cert_serial_and_fingerprint(cert_pem)
    # Load the existing row first so we can reuse an already-imported CA (re-provisioning a device
    # must NOT import a duplicate CA into the box's trust store).
    row = await session.get(DeviceLogForwarding, device_id)
    if row is not None and row.opnsense_ca_uuid:
        ca_uuid = row.opnsense_ca_uuid
    else:
        ca_uuid = await client.import_ca(ca.cert_pem, descr="OPNGMS Syslog CA")
    cert_uuid = await client.import_cert(cert_pem.decode(), key_pem.decode(), descr=f"opngms-logs {device_id}")
    dest_uuid = await client.add_syslog_destination(
        hostname=receiver_host, port=receiver_port, certificate_uuid=cert_uuid)
    if row is None:
        row = DeviceLogForwarding(device_id=device_id, tenant_id=tenant_id)
        session.add(row)
    row.enabled = True
    row.tenant_id = tenant_id
    row.cert_serial, row.cert_fingerprint = serial, fp
    row.opnsense_ca_uuid, row.opnsense_cert_uuid, row.opnsense_dest_uuid = ca_uuid, cert_uuid, dest_uuid
    row.provisioned_at = datetime.now(UTC)
    await session.flush()
    return row


async def deprovision_device(session: AsyncSession, *, device_id: uuid.UUID, client) -> bool:
    """Remove the syslog destination + client cert from the box and mark disabled. Idempotent."""
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None:
        return False
    if row.opnsense_dest_uuid:
        await client.delete_syslog_destination(row.opnsense_dest_uuid)
    if row.opnsense_cert_uuid:
        await client.delete_cert(row.opnsense_cert_uuid)
    row.enabled = False
    row.opnsense_dest_uuid = None
    row.opnsense_cert_uuid = None
    await session.flush()
    return True
