import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.models.metric import Metric
from app.services.monitoring import collect_and_store


class _Ident:
    edition = "Community"
    version = "26.1.9"
    series = "26.1"


class NetClient:
    def set_identity(self, edition, version):
        pass

    async def get_device_identity(self):
        return _Ident()

    async def get_system_info(self):
        return {"cpu_pct": 1.0, "mem_pct": 2.0, "disk_pct": 3.0, "uptime_seconds": 4}

    async def get_interfaces(self):
        return [{"name": "igb0", "up": True, "bytes_in": 100.0, "bytes_out": 200.0}]

    async def get_gateways(self):
        return [{"name": "WAN_GW", "up": False, "rtt_ms": 0.0, "loss_pct": 100.0}]

    async def get_vpn_status(self):
        return [{"name": "wg0", "up": True}]


async def _device(db_engine):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with f() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'unverified','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def test_network_metrics_written_with_labels(db_engine):
    _, did = await _device(db_engine)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        device = await s.get(Device, did)
        state = await collect_and_store(s, device, NetClient(), now=datetime.now(timezone.utc))
        await s.commit()
    async with f() as s:
        rows = (await s.execute(select(Metric).where(Metric.device_id == did))).scalars().all()
        labeled = {(r.metric, r.label): r.value for r in rows}
        assert labeled[("iface.bytes_in", "igb0")] == 100.0
        assert labeled[("gateway.up", "WAN_GW")] == 0.0
        assert labeled[("vpn.up", "wg0")] == 1.0
    assert state.reachable is True
    assert any(g["name"] == "WAN_GW" and g["up"] is False for g in state.gateways)
