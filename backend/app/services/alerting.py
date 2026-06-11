from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.device import Device
from app.services.monitoring import PollState


async def _open_alerts(session: AsyncSession, device: Device) -> dict[tuple[str, str], Alert]:
    result = await session.execute(
        select(Alert).where(Alert.device_id == device.id, Alert.resolved_at.is_(None))
    )
    return {(a.type, a.label): a for a in result.scalars().all()}


def _open(device: Device, type_: str, label: str = "") -> Alert:
    return Alert(tenant_id=device.tenant_id, device_id=device.id, type=type_, label=label)


async def evaluate_alerts(session: AsyncSession, device: Device, state: PollState) -> None:
    """Reconcile the alerts with the current state: open new downs, resolve recovered ones.

    Idempotent: uses ONLY the current state + the open alerts (the partial unique constraint
    prevents duplicates on (device, type, label) anyway).
    """
    now = datetime.now(UTC)
    open_alerts = await _open_alerts(session, device)

    key = ("device.down", "")
    if not state.reachable and key not in open_alerts:
        session.add(_open(device, "device.down"))
    elif state.reachable and key in open_alerts:
        open_alerts[key].resolved_at = now

    if state.reachable:
        down_now = {g["name"] for g in state.gateways if not g["up"]}
        for name in down_now:
            if ("gateway.down", name) not in open_alerts:
                session.add(_open(device, "gateway.down", name))
        for (type_, label), alert in open_alerts.items():
            if type_ == "gateway.down" and label not in down_now:
                alert.resolved_at = now

    await session.flush()
