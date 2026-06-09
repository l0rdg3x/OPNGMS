import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.alert import Alert
from app.models.device import Device
from app.services.alerting import evaluate_alerts
from app.services.monitoring import PollState


async def _device(db_engine):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with f() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) VALUES (:i,:t,'fw','https://fw',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def _active(s, did):
    return (await s.execute(select(Alert).where(Alert.device_id == did, Alert.resolved_at.is_(None)))).scalars().all()


async def test_device_down_opens_then_resolves(db_engine):
    tid, did = await _device(db_engine)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=False))
        await s.commit()
    async with f() as s:
        active = await _active(s, did)
        assert [a.type for a in active] == ["device.down"]
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=False))  # no duplicate
        await s.commit()
    async with f() as s:
        assert len(await _active(s, did)) == 1
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=True))  # resolves
        await s.commit()
    async with f() as s:
        assert await _active(s, did) == []


async def test_gateway_down_opens_and_resolves(db_engine):
    tid, did = await _device(db_engine)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=True, gateways=[{"name": "WAN_GW", "up": False}]))
        await s.commit()
    async with f() as s:
        active = await _active(s, did)
        assert [(a.type, a.label) for a in active] == [("gateway.down", "WAN_GW")]
    async with f() as s:
        device = await s.get(Device, did)
        await evaluate_alerts(s, device, PollState(reachable=True, gateways=[{"name": "WAN_GW", "up": True}]))
        await s.commit()
    async with f() as s:
        assert await _active(s, did) == []
