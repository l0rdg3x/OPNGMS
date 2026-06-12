"""Superadmin cross-tenant log-fleet aggregates (the only cross-tenant views in the console)."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import set_tenant_context
from app.models.device import Device
from app.models.device_log_forwarding import DeviceLogForwarding
from app.repositories.tenant import TenantRepository


async def fleet_forwarding_counts(session: AsyncSession) -> dict[uuid.UUID, dict]:
    """Per-tenant device-forwarding counts. Lists tenants (the tenants table is not RLS-scoped), then
    for each tenant sets the RLS context and counts — no bypass role. Returns {tenant_id: {...}}."""
    tenants = await TenantRepository(session).list()
    out: dict[uuid.UUID, dict] = {}
    for t in tenants:
        await set_tenant_context(session, t.id)
        rows = (await session.execute(
            select(DeviceLogForwarding.enabled, DeviceLogForwarding.revoked_at)
        )).all()
        enabled = sum(1 for e, _ in rows if e)
        revoked = sum(1 for e, r in rows if not e and r is not None)
        disabled = sum(1 for e, r in rows if not e and r is None)
        total_devices = (await session.execute(select(func.count()).select_from(Device))).scalar_one()
        out[t.id] = {
            "tenant_name": t.name,
            "enabled": enabled,
            "disabled": disabled,
            "revoked": revoked,
            "total_devices": int(total_devices),
        }
    return out
