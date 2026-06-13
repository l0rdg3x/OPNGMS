import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.models.metric import Metric
from app.services.monitoring import collect_and_store


class FakeClient:
    async def get_device_identity(self):
        from app.connectors.opnsense.identity import DeviceIdentity
        return DeviceIdentity(edition="community", version="26.1.9", series="26.1")

    def set_identity(self, edition, version):
        pass

    async def get_system_info(self):
        return {"cpu_pct": 10.0, "mem_pct": 50.0, "disk_pct": 20.0, "uptime_seconds": 3600}

    async def get_firmware_status(self):
        return {"product_version": "24.7"}

    async def get_interfaces(self):
        return [{"name": "igb0", "up": True, "bytes_in": 100.0, "bytes_out": 200.0}]

    async def get_gateways(self):
        return [{"name": "WAN_GW", "up": True, "rtt_ms": 5.0, "loss_pct": 0.0}]

    async def get_vpn_status(self):
        return [{"name": "wg0", "up": True}]


async def _make_device(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tenant_id = uuid.uuid4()
    device_id = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id,'A','a','active')"),
            {"id": tenant_id},
        )
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id,:t,'fw','https://fw',''::bytea,''::bytea,true,'unverified','{}')"
            ),
            {"id": device_id, "t": tenant_id},
        )
        await s.commit()
    return tenant_id, device_id


async def test_collect_and_store_writes_metrics_and_updates_status(db_engine):
    tenant_id, device_id = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, device_id)
        await collect_and_store(s, device, FakeClient(), now=datetime.now(timezone.utc))
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(Metric).where(Metric.device_id == device_id))).scalars().all()
        by_metric = {r.metric: r.value for r in rows}
        assert by_metric["cpu.pct"] == 10.0
        assert by_metric["mem.pct"] == 50.0
        assert by_metric["disk.pct"] == 20.0
        assert all(r.tenant_id == tenant_id for r in rows)
        device = await s.get(Device, device_id)
        assert device.status == "reachable"
        assert device.firmware_version == "26.1.9"
        assert device.last_seen is not None


async def test_device_installed_plugins_defaults_to_empty_list(db_engine):
    tenant_id, device_id = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, device_id)
        assert device.installed_plugins == []
