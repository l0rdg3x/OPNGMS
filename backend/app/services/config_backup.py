import gzip

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.core import crypto
from app.models.config_snapshot import ConfigSnapshot
from app.models.device import Device
from app.services.config_diff import canonical_hash


async def backup_config(session: AsyncSession, device: Device, client) -> bool:
    """Fetch the device config, store a new encrypted snapshot only if it changed.

    Returns True if a new version was stored, False otherwise (no change, or a
    connector error which is swallowed so the cron job survives).
    """
    try:
        xml = await client.get_config_backup()
    except OpnsenseError:
        return False
    try:
        digest = canonical_hash(xml)
    except (ValueError, SyntaxError):
        # Malformed or hostile XML (XXE / billion-laughs refused by defusedxml):
        # skip this device, never crash the job, never store.
        return False
    latest = (
        await session.execute(
            select(ConfigSnapshot.canonical_hash)
            .where(ConfigSnapshot.device_id == device.id)
            .order_by(ConfigSnapshot.taken_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest == digest:
        return False  # dedup-on-change
    content_enc = crypto.encrypt_bytes(gzip.compress(xml.encode("utf-8")))
    session.add(
        ConfigSnapshot(
            tenant_id=device.tenant_id,
            device_id=device.id,
            canonical_hash=digest,
            content_enc=content_enc,
            opnsense_version=device.firmware_version or "",
            size_bytes=len(xml.encode("utf-8")),
        )
    )
    await session.flush()
    return True
