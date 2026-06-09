import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.alert import Alert
from app.models.device import Device
from app.services.alerting import evaluate_alerts
from app.services.monitoring import collect_and_store


class DownGwClient:
    async def get_system_info(self): return {"cpu_pct": 1.0, "mem_pct": 2.0, "disk_pct": 3.0, "uptime_seconds": 4}
    async def get_firmware_status(self): return {"product_version": "24.7"}
    async def get_interfaces(self): return []
    async def get_gateways(self): return [{"name": "WAN_GW", "up": False, "rtt_ms": 0.0, "loss_pct": 100.0}]
    async def get_vpn_status(self): return []


async def test_poll_collects_and_opens_gateway_alert(db_engine):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with f() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    async with f() as s:
        device = await s.get(Device, did)
        state = await collect_and_store(s, device, DownGwClient(), now=datetime.now(timezone.utc))
        await evaluate_alerts(s, device, state)
        await s.commit()
    async with f() as s:
        active = (await s.execute(select(Alert).where(Alert.device_id == did, Alert.resolved_at.is_(None)))).scalars().all()
        assert [(a.type, a.label) for a in active] == [("gateway.down", "WAN_GW")]
        assert all(a.tenant_id == tid for a in active)
