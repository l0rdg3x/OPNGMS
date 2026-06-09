from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.metric import Metric


@dataclass
class PollState:
    reachable: bool
    gateways: list[dict] = field(default_factory=list)


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
) -> PollState:
    """Pollla un device, scrive le metriche di salute, aggiorna lo stato.

    Non solleva sugli errori del connector: marca il device 'unverified' (rete
    irraggiungibile non deve far fallire il ciclo). `client` è iniettabile (test/poller).
    """
    try:
        info = await client.get_system_info()
        fw = await client.get_firmware_status()
        interfaces = await client.get_interfaces()
        gateways = await client.get_gateways()
        vpn = await client.get_vpn_status()
    except OpnsenseError:
        device.status = "unverified"
        return PollState(reachable=False)
    rows = [
        _metric(now, device, "cpu.pct", info["cpu_pct"]),
        _metric(now, device, "mem.pct", info["mem_pct"]),
        _metric(now, device, "disk.pct", info["disk_pct"]),
        _metric(now, device, "uptime.seconds", info["uptime_seconds"]),
    ]
    for it in interfaces:
        rows.append(_metric(now, device, "iface.bytes_in", it["bytes_in"], it["name"]))
        rows.append(_metric(now, device, "iface.bytes_out", it["bytes_out"], it["name"]))
        rows.append(_metric(now, device, "iface.up", 1.0 if it["up"] else 0.0, it["name"]))
    for g in gateways:
        rows.append(_metric(now, device, "gateway.rtt_ms", g["rtt_ms"], g["name"]))
        rows.append(_metric(now, device, "gateway.loss_pct", g["loss_pct"], g["name"]))
        rows.append(_metric(now, device, "gateway.up", 1.0 if g["up"] else 0.0, g["name"]))
    for v in vpn:
        rows.append(_metric(now, device, "vpn.up", 1.0 if v["up"] else 0.0, v["name"]))
    session.add_all(rows)
    device.status = "reachable"
    device.last_seen = now
    version = fw.get("product_version")
    if version:
        device.firmware_version = version
    await session.flush()
    return PollState(reachable=True, gateways=gateways)
