import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
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
        return {"cpu_pct": 5.0, "mem_pct": 30.0, "disk_pct": 10.0, "uptime_seconds": 100}

    async def get_firmware_status(self):
        return {"product_version": "24.7"}

    async def get_interfaces(self):
        return []

    async def get_gateways(self):
        return []

    async def get_vpn_status(self):
        return []

    async def get_plugin_info(self):
        return {"product_version": "26.1.9", "plugins": [], "available": []}


class FailClient:
    async def get_device_identity(self):
        raise ReachabilityError("down")

    def set_identity(self, edition, version):
        pass

    async def get_system_info(self):
        raise ReachabilityError("down")

    async def get_firmware_status(self):
        return {}

    async def get_interfaces(self):
        return []

    async def get_gateways(self):
        return []

    async def get_vpn_status(self):
        return []


async def _make_device(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(
            text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
                 "VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'unverified','{}')"),
            {"i": did, "t": tid},
        )
        await s.commit()
    return tid, did


async def test_two_polls_produce_two_time_buckets(db_engine):
    _, did = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for offset in (0, 1):
        async with factory() as s:
            device = await s.get(Device, did)
            now = datetime(2026, 6, 9, 12, offset, 0, tzinfo=timezone.utc)
            await collect_and_store(s, device, FakeClient(), now=now)
            await s.commit()
    async with factory() as s:
        cpu_points = (
            await s.execute(
                select(func.count()).select_from(Metric).where(Metric.device_id == did, Metric.metric == "cpu.pct")
            )
        ).scalar_one()
        assert cpu_points == 2


async def test_collect_and_store_error_path_unverified_no_metrics(db_engine):
    _, did = await _make_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        await collect_and_store(s, device, FailClient(), now=datetime.now(timezone.utc))
        await s.commit()
    async with factory() as s:
        count = (await s.execute(select(func.count()).select_from(Metric).where(Metric.device_id == did))).scalar_one()
        assert count == 0  # no metric written on error
        device = await s.get(Device, did)
        assert device.status == "unverified"


async def test_poll_device_wiring(db_engine, monkeypatch):
    _, did = await _make_device(db_engine)
    # monkeypatch decrypt (the test device's secrets are '' = not decryptable) and the client
    monkeypatch.setattr("app.worker.crypto.decrypt", lambda b: "x")
    monkeypatch.setattr("app.worker.OpnsenseClient", lambda *a, **k: FakeClient())
    from app.worker import poll_device

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ctx = {"session_factory": factory}
    status = await poll_device(ctx, str(did))
    assert status == "reachable"
    async with factory() as s:
        count = (await s.execute(select(func.count()).select_from(Metric).where(Metric.device_id == did))).scalar_one()
        assert count == 4  # cpu/mem/disk/uptime
