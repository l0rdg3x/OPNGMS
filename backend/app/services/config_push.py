import gzip
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
from app.services.app_settings import get_live_push
from app.services.config_apply import UnknownChangeKindError, apply_for_kind
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

    Aliases carry no secrets; for secret-bearing kinds we redact. A catalog_setting can target an
    arbitrary field — including raw/password-like fields — so we surface only WHICH fields change
    (paths) and a grid-op summary, never the entered values.
    """
    base = {"operation": change.operation, "kind": change.kind, "target": change.target}
    if change.kind == "catalog_setting":
        payload = change.payload or {}
        return {
            **base,
            "scalar_fields": sorted((payload.get("scalars") or {}).keys()),
            "grid_ops": [{"op": g.get("op"), "grid": g.get("row")}
                         for g in (payload.get("grids") or [])],
        }
    return {**base, "new": change.payload}


def _advisory_key(device_id: uuid.UUID) -> int:
    """Stable signed 64-bit key for pg_try_advisory_xact_lock, derived from device_id.

    A UUID is already a uniform 128-bit value, so the first 8 bytes are a fine lock-partition key —
    no hash needed (avoids a spurious weak-hash finding; the key is never authenticated or stored).
    """
    return int.from_bytes(device_id.bytes[:8], "big", signed=True)


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
    # An empty baseline means no snapshot existed at proposal time (e.g. a brand-new device whose
    # daily backup hasn't run yet) — there's nothing to compare against, so allow the apply (the
    # pre-apply snapshot below still captures a rollback point). Only guard when we have a baseline.
    if change.baseline_hash and current != change.baseline_hash:
        change.status = "conflict"
        change.result = {
            "reason": "config changed since proposal",
            "baseline": change.baseline_hash,
        }
        await session.flush()
        return "conflict"
    change.status = "applying"
    await session.flush()
    live = await get_live_push(session, env_default=get_settings().live_push_enabled)
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
    except UnknownChangeKindError:
        # No applier for this kind -> a permanent failure; mark failed instead of letting the worker
        # retry forever (each retry would re-save a pre-apply snapshot, polluting the table).
        change.status = "failed"
        change.result = {"error": "unknown change kind"}
    await session.flush()
    return change.status


async def apply_profile_sequence(
    session: AsyncSession, changes: list[ConfigChange], client, now: datetime
) -> dict:
    """Apply a profile's member changes IN ORDER under ONE device advisory lock.

    The members share a baseline_hash captured at proposal time (before any of them applied). Applying
    them as independent jobs makes members 2..N falsely conflict (member 1 mutates config.xml). Here we
    check external staleness ONCE against that shared baseline, then apply the members sequentially —
    without re-checking the baseline between siblings (the lock makes us the only writer), so a member
    that changes the config can't make the next sibling conflict. One pre-apply snapshot is the
    profile's rollback point; on the first member failure the rest are aborted (marked failed)."""
    scheduled = [c for c in changes if c.status == "scheduled"]
    if not scheduled:
        return {"applied": 0, "failed": 0, "conflict": 0, "status": "noop"}
    device_id = scheduled[0].device_id
    got = (await session.execute(
        text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": _advisory_key(device_id)})).scalar_one()
    if not got:
        return {"applied": 0, "failed": 0, "conflict": 0, "status": "locked"}  # leave scheduled to retry
    try:
        xml = await client.get_config_backup()
        current = canonical_hash(xml)
    except (OpnsenseError, ValueError, SyntaxError):
        for c in scheduled:
            c.status, c.result = "failed", {"error": "could not read current config"}
        await session.flush()
        return {"applied": 0, "failed": len(scheduled), "conflict": 0, "status": "read-failed"}
    baseline = scheduled[0].baseline_hash
    if baseline and current != baseline:
        for c in scheduled:
            c.status, c.result = "conflict", {"reason": "config changed since proposal", "baseline": baseline}
        await session.flush()
        return {"applied": 0, "failed": 0, "conflict": len(scheduled), "status": "conflict"}
    live = await get_live_push(session, env_default=get_settings().live_push_enabled)
    snap_id = await _save_pre_apply_snapshot(session, scheduled[0], xml) if live else None
    applied = failed = 0
    aborted = False
    for c in scheduled:
        c.pre_apply_snapshot_id = snap_id
        if aborted:
            c.status, c.result = "failed", {"error": "aborted: an earlier profile member failed"}
            failed += 1
            continue
        try:
            c.result = await apply_for_kind(client, c.kind, c.operation, c.payload, dry_run=not live)
            c.status, c.applied_at = "applied", now
            applied += 1
        except OpnsenseError:
            c.status, c.result = "failed", {"error": "apply failed"}
            failed += 1
            aborted = True
        except UnknownChangeKindError:
            c.status, c.result = "failed", {"error": "unknown change kind"}
            failed += 1
            aborted = True
    await session.flush()
    return {"applied": applied, "failed": failed, "conflict": 0, "status": "done"}
