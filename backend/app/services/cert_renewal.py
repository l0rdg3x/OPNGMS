"""Proactive device-cert renewal: rotate forwarding certs before they expire (worker-driven)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.device_log_forwarding import DeviceLogForwarding
from app.services.log_forwarding import rotate_device_cert


def due_for_renewal(cert_not_after: datetime | None, *, now: datetime, window: timedelta) -> bool:
    """True iff the cert has a known expiry that falls within `window` of `now` (or is already past).

    A null expiry is never auto-renewed (it predates 3.1 — left for manual rotation).
    """
    return cert_not_after is not None and cert_not_after < now + window


async def renew_expiring_device_certs(session: AsyncSession, settings, *, client_for) -> dict:
    """Rotate the cert of every ENABLED device whose cert is within the renewal window.

    `client_for` builds an OpnsenseClient for a Device (injected for tests). Runs under the owner
    session (RLS-exempt, sees all tenants) — the worker's context. Each device is isolated in its own
    SAVEPOINT: a box failure rolls back only that device's partial write, counts it `failed`, and the
    batch continues. The caller commits the successful renewals.
    """
    now = datetime.now(UTC)
    window = timedelta(days=settings.cert_renewal_window_days)
    rows = (await session.execute(
        select(DeviceLogForwarding.device_id, DeviceLogForwarding.tenant_id, DeviceLogForwarding.cert_not_after)
        .where(DeviceLogForwarding.enabled.is_(True))
    )).all()
    summary = {"considered": 0, "renewed": 0, "failed": 0}
    for device_id, tenant_id, cert_not_after in rows:
        if not due_for_renewal(cert_not_after, now=now, window=window):
            continue
        summary["considered"] += 1
        device = await session.get(Device, device_id)
        if device is None:
            summary["failed"] += 1
            continue
        try:
            # SAVEPOINT per device: a failure rolls back only this device's flushed changes, leaving
            # earlier successful renewals (also flushed in this session) intact for the caller's commit.
            async with session.begin_nested():
                await rotate_device_cert(session, tenant_id=tenant_id, device_id=device_id,
                                         client=client_for(device), receiver_host=settings.syslog_receiver_host,
                                         receiver_port=settings.syslog_tls_port)
            summary["renewed"] += 1
        except (OpnsenseError, ValueError):
            summary["failed"] += 1
    return summary
