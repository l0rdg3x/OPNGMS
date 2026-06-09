import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.config_change import ConfigChange
from app.repositories.config_snapshot import ConfigSnapshotRepository


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
