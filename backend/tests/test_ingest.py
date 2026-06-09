import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, alerts, fail=False):
        self._alerts = alerts
        self._fail = fail

    async def get_ids_alerts(self, since=None):
        if self._fail:
            raise ReachabilityError("boom")
        return self._alerts


def _alert(ts, key, src="10.0.0.5", name="ET SCAN"):
    return {
        "time": ts, "category": "alert", "src_ip": src, "dst_ip": "1.2.3.4",
        "name": name, "severity": "2", "action": "allowed", "event_key": key, "attributes": {},
    }


async def _device(db_engine, tenant_id) -> Device:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def test_ingest_writes_events_and_advances_cursor(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        client = FakeClient([_alert(now, "k1"), _alert(now, "k2")])
        n = await ingest_events(s, device, client, now)
        await s.commit()
    assert n == 2
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
        cur = (await s.execute(
            text("SELECT last_time FROM ingest_cursors WHERE device_id=:d AND source='ids'"),
            {"d": did},
        )).scalar_one()
    assert cnt == 2
    assert cur == now


async def test_ingest_idempotent(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for _ in range(2):  # due run con gli stessi eventi
        async with factory() as s:
            device = await s.get(Device, did)
            await ingest_events(s, device, FakeClient([_alert(now, "k1")]), now)
            await s.commit()
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert cnt == 1  # nessun duplicato


async def test_ingest_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient([], fail=True), now)  # source solleva
        await s.commit()
    assert n == 0  # nessun crash, zero eventi
