import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, service=None):
        self._service = service or []

    async def get_ids_alerts(self, since=None):
        return []

    async def get_dns_events(self, since=None):
        return []

    async def get_service_events(self, since=None):
        return self._service


def _svc(ts, key, name="service_crashed", category="service", severity="high"):
    return {
        "time": ts, "category": category, "name": name, "severity": severity,
        "event_key": key, "attributes": {"process": "kernel", "message": "exited on signal 11"},
    }


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw-1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def _alerts(factory, did):
    async with factory() as s:
        return (await s.execute(
            text("SELECT type, label FROM alerts WHERE device_id=:d AND resolved_at IS NULL ORDER BY label"),
            {"d": did},
        )).all()


async def test_new_high_severity_service_event_raises_one_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        await ingest_events(s, device, FakeClient(service=[_svc(now, "s1")]), now)
        await s.commit()
    rows = await _alerts(factory, did)
    assert len(rows) == 1
    assert rows[0][0] == "service_event"
    assert rows[0][1] == "service_crashed: fw-1"


async def test_repeat_poll_of_same_event_raises_no_new_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    for _ in range(2):  # same event polled twice
        async with factory() as s:
            device = await s.get(Device, did)
            await ingest_events(s, device, FakeClient(service=[_svc(now, "s1")]), now)
            await s.commit()
    rows = await _alerts(factory, did)
    assert len(rows) == 1  # deduped, not duplicated


async def test_non_high_severity_service_event_raises_no_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        ev = _svc(now, "s1", name="service_restarted", severity="medium")
        await ingest_events(s, device, FakeClient(service=[ev]), now)
        await s.commit()
    rows = await _alerts(factory, did)
    assert rows == []  # only high-severity events alert


async def test_two_distinct_high_events_raise_two_alerts(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        evs = [
            _svc(now, "s1", name="service_crashed", category="service"),
            _svc(now + timedelta(seconds=1), "s2", name="reboot", category="reboot"),
        ]
        await ingest_events(s, device, FakeClient(service=evs), now)
        await s.commit()
    rows = await _alerts(factory, did)
    labels = {r[1] for r in rows}
    assert labels == {"service_crashed: fw-1", "reboot: fw-1"}


async def test_alert_failure_does_not_abort_ingest(db_engine, two_tenants, monkeypatch):
    # If raising the alert fails, the events are still committed (best-effort alerting).
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    import app.services.ingest as ingest_mod

    async def boom(*args, **kwargs):
        raise RuntimeError("alert backend down")

    monkeypatch.setattr(ingest_mod, "raise_service_alerts", boom)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(service=[_svc(now, "s1")]), now)
        await s.commit()
    assert n == 1  # the event was ingested despite the alert failure
    async with factory() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM events WHERE source='service'"))).scalar_one()
    assert cnt == 1
    rows = await _alerts(factory, did)
    assert rows == []  # no alert (the raise failed, but ingest survived)
