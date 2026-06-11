import gzip
import hashlib
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.core import crypto
from app.core.config import get_settings
from app.models.config_change import ConfigChange
from app.models.config_snapshot import ConfigSnapshot
from app.models.device import Device
from app.repositories.config_snapshot import ConfigSnapshotRepository
from app.services.config_apply import apply_for_kind
from app.services.config_diff import canonical_hash


async def create_change(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    created_by: uuid.UUID,
    kind: str,
    operation: str,
    target: str,
    payload: dict,
) -> ConfigChange:
    """Create a draft change, capturing the baseline canonical_hash (4A) for the staleness guard."""
    snap = await ConfigSnapshotRepository(session, tenant_id).latest(device_id)
    baseline = snap.canonical_hash if snap else ""
    change = ConfigChange(
        tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind=kind, operation=operation, target=target, payload=payload,
        baseline_hash=baseline, status="draft",
    )
    session.add(change)
    await session.flush()
    return change


def preview_change(change: ConfigChange) -> dict:
    """Secret-safe summary of what the change would do (no firewall contact, no secret values).

    Aliases carry no secrets; for secret-bearing kinds later, redact sensitive payload keys here.
    """
    return {
        "operation": change.operation,
        "kind": change.kind,
        "target": change.target,
        "new": change.payload,
    }


def _advisory_key(device_id: uuid.UUID) -> int:
    """Stable signed 64-bit key for pg_try_advisory_xact_lock, derived from device_id."""
    digest = hashlib.sha1(str(device_id).encode()).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


async def _save_pre_apply_snapshot(session: AsyncSession, change: ConfigChange, xml: str) -> uuid.UUID:
    """Persist the current device config as an encrypted snapshot (a pre-apply rollback point)."""
    raw = xml.encode("utf-8")
    device = await session.get(Device, change.device_id)
    snap = ConfigSnapshot(
        tenant_id=change.tenant_id,
        device_id=change.device_id,
        canonical_hash=canonical_hash(xml),
        content_enc=crypto.encrypt_bytes(gzip.compress(raw)),
        opnsense_version=(device.firmware_version or "") if device is not None else "",
        size_bytes=len(raw),
    )
    session.add(snap)
    await session.flush()
    return snap.id


async def apply_change(
    session: AsyncSession, change: ConfigChange, client, now: datetime
) -> str:
    """Apply a scheduled change. Returns the new status.

    Real apply only when LIVE_PUSH_ENABLED is set (otherwise dry-run); staleness-guarded;
    per-device serialized. SAFETY-CRITICAL: re-reads the live config and refuses to apply
    if it drifted from the baseline captured at proposal time (no clobber).
    """
    if change.status != "scheduled":
        return change.status
    # Per-device serialization: transaction-scoped advisory lock (auto-released at commit/rollback).
    got = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:k)"),
            {"k": _advisory_key(change.device_id)},
        )
    ).scalar_one()
    if not got:
        return change.status  # another apply holds the device lock; leave scheduled for retry
    # Staleness guard: re-read the current config and compare canonical hashes.
    try:
        xml = await client.get_config_backup()
        current = canonical_hash(xml)
    except (OpnsenseError, ValueError, SyntaxError):
        change.status = "failed"
        change.result = {"error": "could not read current config"}
        await session.flush()
        return "failed"
    if current != change.baseline_hash:
        change.status = "conflict"
        change.result = {
            "reason": "config changed since proposal",
            "baseline": change.baseline_hash,
        }
        await session.flush()
        return "conflict"
    change.status = "applying"
    await session.flush()
    live = get_settings().live_push_enabled
    try:
        if live:
            # rollback point: persist the pre-apply config (the `xml` already read above).
            change.pre_apply_snapshot_id = await _save_pre_apply_snapshot(session, change, xml)
        res = await apply_for_kind(client, change.kind, change.operation, change.payload, dry_run=not live)
        change.status = "applied"
        change.applied_at = now
        change.result = res
    except OpnsenseError:
        change.status = "failed"
        change.result = {"error": "apply failed"}
    await session.flush()
    return change.status
