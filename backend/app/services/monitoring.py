import asyncio
import contextlib
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
    """Poll a device, write the health metrics, update the status.

    Does not raise on connector errors: marks the device 'unverified' (an unreachable
    network must not fail the cycle). `client` is injectable (test/poller).
    """
    try:
        ident = await client.get_device_identity()
        client.set_identity(ident.edition, ident.version)
        # identity must resolve first (it configures the version-aware resolver); the four telemetry
        # reads are independent, so fetch them concurrently — each connector call uses its own httpx
        # client, so this is safe and cuts the per-poll wall time from 5 sequential HTTP round-trips to 2.
        # Any OpnsenseError still propagates out of gather -> the same unverified outcome as before.
        info, interfaces, gateways, vpn = await asyncio.gather(
            client.get_system_info(),
            client.get_interfaces(),
            client.get_gateways(),
            client.get_vpn_status(),
        )
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
    device.edition = ident.edition
    device.firmware_series = ident.series
    version = ident.version
    if version:
        device.firmware_version = version
    # Plugin inventory is best-effort: a failure must not fail the poll or wipe the last good list.
    with contextlib.suppress(OpnsenseError):
        device.installed_plugins = (await client.get_plugin_info()).get("available", [])
    await session.flush()
    return PollState(reachable=True, gateways=gateways)
