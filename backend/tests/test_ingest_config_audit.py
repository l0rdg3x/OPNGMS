import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, config=None, fail=False):
        self._config = config or []
        self._fail = fail

    async def get_ids_alerts(self, since=None):
        return []

    async def get_dns_events(self, since=None):
        return []

    async def get_service_events(self, since=None):
        return []

    async def get_config_changes(self, since=None):
        if self._fail:
            raise ReachabilityError("boom")
        return self._config


def _cfg(ts, key, name="admin", channel="gui", severity="medium"):
    return {
        "time": ts, "category": "firewall", "src_ip": "10.0.0.5", "name": name,
        "severity": severity, "action": channel, "event_key": key,
        "attributes": {"actor": name, "channel": channel, "change_ref": "/firewall_rules.php"},
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


async def test_ingest_config_audit_writes_events_and_advances_cursor(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(config=[_cfg(now, "c1"), _cfg(now, "c2")]), now)
        await s.commit()
    assert n == 2
    async with factory() as s:
        srcs = (await s.execute(
            text("SELECT source FROM events WHERE source='config_audit'"))).scalars().all()
        cur = (await s.execute(
            text("SELECT last_time FROM ingest_cursors WHERE device_id=:d AND source='config_audit'"),
            {"d": did})).scalar_one()
    assert srcs == ["config_audit", "config_audit"] and cur == now


async def test_ingest_config_audit_drift_raises_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        await ingest_events(s, device, FakeClient(config=[_cfg(now, "c1")]), now)
        await s.commit()
    async with factory() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM alerts WHERE type='config_audit' AND device_id=:d"),
            {"d": did})).scalar_one()
    assert cnt == 1


async def test_ingest_config_audit_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(fail=True), now)   # source raises -> skipped
        await s.commit()
    assert n == 0


async def test_ingest_config_audit_attributes_and_alerts_external(db_engine, two_tenants):
    """An api change from a non-management IP becomes api_external (drift) and raises an alert; the device
    must already have a learned mgmt_source_ip."""
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("UPDATE devices SET mgmt_source_ip='10.0.0.1' WHERE id=:d"), {"d": did})
        await s.commit()
    cfg = {
        "time": now, "category": "firewall", "src_ip": "203.0.113.9", "name": "root",
        "severity": "info", "action": "api", "event_key": "ext1",
        "attributes": {"channel": "api", "change_ref": "/api/firewall/filter/addRule"},
    }
    async with factory() as s:
        device = await s.get(Device, did)
        await ingest_events(s, device, FakeClient(config=[cfg]), now)
        await s.commit()
    async with factory() as s:
        action = (await s.execute(text(
            "SELECT action FROM events WHERE source='config_audit' AND device_id=:d"),
            {"d": did})).scalar_one()
        alerts = (await s.execute(text(
            "SELECT count(*) FROM alerts WHERE type='config_audit' AND device_id=:d"),
            {"d": did})).scalar_one()
    assert action == "api_external" and alerts == 1
