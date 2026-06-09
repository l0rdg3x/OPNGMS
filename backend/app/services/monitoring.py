from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.metric import Metric


def _metric(now: datetime, device: Device, name: str, value, label: str = "") -> Metric:
    return Metric(
        time=now,
        device_id=device.id,
        tenant_id=device.tenant_id,
        metric=name,
        label=label,
        value=float(value),
    )


async def collect_and_store(
    session: AsyncSession, device: Device, client, now: datetime
) -> None:
    """Pollla un device, scrive le metriche di salute, aggiorna lo stato.

    Non solleva sugli errori del connector: marca il device 'unverified' (rete
    irraggiungibile non deve far fallire il ciclo). `client` è iniettabile (test/poller).
    """
    try:
        info = await client.get_system_info()
        fw = await client.get_firmware_status()
    except OpnsenseError:
        device.status = "unverified"
        return
    session.add_all(
        [
            _metric(now, device, "cpu.pct", info["cpu_pct"]),
            _metric(now, device, "mem.pct", info["mem_pct"]),
            _metric(now, device, "disk.pct", info["disk_pct"]),
            _metric(now, device, "uptime.seconds", info["uptime_seconds"]),
        ]
    )
    device.status = "reachable"
    device.last_seen = now
    version = fw.get("product_version")
    if version:
        device.firmware_version = version
    await session.flush()
