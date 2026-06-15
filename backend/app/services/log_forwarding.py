"""DB-backed CA (key encrypted at rest) + per-device provisioning orchestration."""
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.core.config import get_settings
from app.models.device_log_forwarding import DeviceLogForwarding
from app.models.revoked_syslog_cert import RevokedSyslogCert
from app.models.syslog_ca import SINGLETON_ID, SyslogCa
from app.models.syslog_ca_key import SyslogCaKey
from app.services.syslog_ca import (
    build_ca,
    cert_not_after,
    cert_serial_and_fingerprint,
    issue_device_cert,
)


class SyslogCaService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SyslogCa | None:
        return (await self.session.execute(select(SyslogCa))).scalar_one_or_none()

    async def require_ca(self) -> SyslogCa:
        """Return the existing CA or fail. The CA is created owner-side (bootstrap/worker via
        ``ensure_ca``); the app role cannot create it because it cannot write ``syslog_ca_key``."""
        row = await self.get()
        if row is None:
            raise RuntimeError("syslog CA not initialized — run syslog-bootstrap")
        return row

    async def ensure_ca(self) -> SyslogCa:
        """Owner-only create path: build the CA and insert BOTH the cert row (``syslog_ca``) and the
        encrypted-key row (``syslog_ca_key``). Idempotent. Not callable by the app role (no INSERT on
        ``syslog_ca_key``) — that is intentional; the API uses ``require_ca``."""
        row = await self.get()
        if row is not None:
            return row
        cert_pem, key_pem = build_ca()
        row = SyslogCa(id=SINGLETON_ID, cert_pem=cert_pem.decode())
        self.session.add(row)
        self.session.add(SyslogCaKey(id=SINGLETON_ID, key_enc=crypto.encrypt_bytes(key_pem)))
        await self.session.flush()
        return row

    async def _ca_key_enc(self) -> bytes:
        """The encrypted CA private key, read via the SECURITY DEFINER accessor so the app role can
        sign without SELECT on the owner-only key table. Works for the owner role too."""
        return (await self.session.execute(text("SELECT opngms_syslog_ca_key()"))).scalar_one()

    async def device_cert(self, ca: SyslogCa, *, tenant_id: uuid.UUID,
                          device_id: uuid.UUID) -> tuple[bytes, bytes]:
        return issue_device_cert(ca.cert_pem.encode(), crypto.decrypt_bytes(bytes(await self._ca_key_enc())),
                                 tenant_id=str(tenant_id), device_id=str(device_id),
                                 days=get_settings().device_cert_days)


async def provision_device(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                           client, receiver_host: str, receiver_port: int) -> DeviceLogForwarding:
    """Issue a device cert, import the CA + cert into the box, configure the mTLS syslog destination,
    and record state. `client` is an OpnsenseClient (or a stub with the same methods)."""
    svc = SyslogCaService(session)
    ca = await svc.require_ca()
    cert_pem, key_pem = await svc.device_cert(ca, tenant_id=tenant_id, device_id=device_id)
    serial, fp = cert_serial_and_fingerprint(cert_pem)
    not_after = cert_not_after(cert_pem)
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
    row.cert_not_after = not_after
    row.opnsense_ca_uuid, row.opnsense_cert_uuid, row.opnsense_dest_uuid = ca_uuid, cert_uuid, dest_uuid
    row.provisioned_at = datetime.now(UTC)
    row.revoked_at = None
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


async def rotate_device_cert(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                             client, receiver_host: str, receiver_port: int) -> DeviceLogForwarding:
    """Issue a fresh device cert and swap it on the box: add the new destination BEFORE deleting the
    old one (no log gap). Requires the device to be currently forwarding."""
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None or not row.enabled:
        raise ValueError("device is not currently forwarding")
    svc = SyslogCaService(session)
    ca = await svc.require_ca()
    cert_pem, key_pem = await svc.device_cert(ca, tenant_id=tenant_id, device_id=device_id)
    serial, fp = cert_serial_and_fingerprint(cert_pem)
    not_after = cert_not_after(cert_pem)
    old_cert_uuid, old_dest_uuid = row.opnsense_cert_uuid, row.opnsense_dest_uuid
    new_cert_uuid = await client.import_cert(cert_pem.decode(), key_pem.decode(),
                                             descr=f"opngms-logs {device_id}")
    new_dest_uuid = await client.add_syslog_destination(
        hostname=receiver_host, port=receiver_port, certificate_uuid=new_cert_uuid)
    if old_dest_uuid:
        await client.delete_syslog_destination(old_dest_uuid)
    if old_cert_uuid:
        await client.delete_cert(old_cert_uuid)
    row.cert_serial, row.cert_fingerprint, row.cert_not_after = serial, fp, not_after
    row.opnsense_cert_uuid, row.opnsense_dest_uuid = new_cert_uuid, new_dest_uuid
    row.provisioned_at = datetime.now(UTC)
    await session.flush()
    return row


async def revoke_device(session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID,
                        client, reason: str | None) -> DeviceLogForwarding:
    """Soft-revoke: snapshot the serial into the ledger, deprovision the box, mark the row revoked.
    One box-gated unit of work (the caller commits only on success)."""
    row = await session.get(DeviceLogForwarding, device_id)
    if row is None or not row.enabled:
        raise ValueError("device is not currently forwarding")
    revoked_serial = row.cert_serial
    # Box calls first, THEN record the ledger entry: this keeps the ledger insert strictly inside the
    # box-gated unit of work (a box failure raises before the add, so no ledger row is ever staged for
    # a revocation that did not take effect). 3.2-bis deliberately inverts this for CRL-first enforcement.
    if row.opnsense_dest_uuid:
        await client.delete_syslog_destination(row.opnsense_dest_uuid)
    if row.opnsense_cert_uuid:
        await client.delete_cert(row.opnsense_cert_uuid)
    session.add(RevokedSyslogCert(tenant_id=tenant_id, device_id=device_id,
                                  serial=revoked_serial, reason=reason))
    row.enabled = False
    row.opnsense_dest_uuid = None
    row.opnsense_cert_uuid = None
    row.revoked_at = datetime.now(UTC)
    await session.flush()
    return row
