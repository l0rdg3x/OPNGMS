import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    """A client exposing only the service source (the other sources return nothing)."""

    def __init__(self, service=None, fail_service=False):
        self._service = service or []
        self._fail_service = fail_service

    async def get_ids_alerts(self, since=None):
        return []

    async def get_dns_events(self, since=None):
        return []

    async def get_service_events(self, since=None):
        if self._fail_service:
            raise ReachabilityError("boom")
        return self._service


def _svc(ts, key, name="reboot", category="reboot", severity="high"):
    return {
        "time": ts, "category": category, "name": name, "severity": severity,
        "event_key": key, "attributes": {"process": "shutdown", "message": "reboot by root"},
    }


async def _device(db_engine, tenant_id) -> uuid.UUID:
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


async def test_ingest_service_writes_events_and_advances_cursor(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(service=[_svc(now, "s1"), _svc(now, "s2")]), now)
        await s.commit()
    assert n == 2
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events WHERE source='service'"))).scalars().all()
        cur = (await s.execute(
            text("SELECT last_time FROM ingest_cursors WHERE device_id=:d AND source='service'"),
            {"d": did},
        )).scalar_one()
    assert srcs == ["service", "service"]
    assert cur == now


async def test_ingest_service_idempotent(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for _ in range(2):  # same event polled twice
        async with factory() as s:
            device = await s.get(Device, did)
            await ingest_events(s, device, FakeClient(service=[_svc(now, "s1")]), now)
            await s.commit()
    async with factory() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM events WHERE source='service'"))).scalar_one()
    assert cnt == 1  # no duplicate across polls


async def test_ingest_service_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(fail_service=True), now)  # source raises
        await s.commit()
    assert n == 0  # no crash, zero events
