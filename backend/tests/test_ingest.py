import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, alerts=None, dns=None, service=None, fail_ids=False, fail_dns=False):
        self._alerts = alerts or []
        self._dns = dns or []
        self._service = service or []
        self._fail_ids = fail_ids
        self._fail_dns = fail_dns

    async def get_ids_alerts(self, since=None):
        if self._fail_ids:
            raise ReachabilityError("boom")
        return self._alerts

    async def get_dns_events(self, since=None):
        if self._fail_dns:
            raise ReachabilityError("boom")
        return self._dns

    async def get_service_events(self, since=None):
        return self._service


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
    for _ in range(2):  # two runs with the same events
        async with factory() as s:
            device = await s.get(Device, did)
            await ingest_events(s, device, FakeClient([_alert(now, "k1")]), now)
            await s.commit()
    async with factory() as s:
        cnt = (await s.execute(text("SELECT count(*) FROM events"))).scalar_one()
    assert cnt == 1  # no duplicate


async def test_ingest_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(fail_ids=True), now)  # source raises
        await s.commit()
    assert n == 0  # no crash, zero events


def _dns(ts, key, client="10.0.0.20", domain="example.com", action="allowed"):
    return {
        "time": ts, "category": "query", "src_ip": client, "dst_ip": "",
        "name": domain, "severity": "", "action": action, "event_key": key, "attributes": {},
    }


async def test_ingest_dns_writes_events(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(dns=[_dns(now, "d1")]), now)
        await s.commit()
    assert n == 1
    async with factory() as s:
        src = (await s.execute(text("SELECT source FROM events WHERE source='dns'"))).scalars().all()
    assert src == ["dns"]


async def test_ingest_both_sources_in_one_run(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(alerts=[_alert(now, "k1")], dns=[_dns(now, "d1")]), now)
        await s.commit()
    assert n == 2  # 1 ids + 1 dns
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events ORDER BY source"))).scalars().all()
    assert srcs == ["dns", "ids"]


async def test_ingest_dns_fails_ids_succeeds(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        # DNS raises, IDS succeeds: per-source resilience guarantees IDS is still ingested
        n = await ingest_events(s, device, FakeClient(alerts=[_alert(now, "k1")], fail_dns=True), now)
        await s.commit()
    assert n == 1
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events"))).scalars().all()
    assert srcs == ["ids"]
