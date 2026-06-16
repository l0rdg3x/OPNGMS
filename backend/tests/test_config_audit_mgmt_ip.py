import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.services.ingest import _attribute_mgmt_ip

BASE = datetime(2026, 6, 16, 12, 0, tzinfo=UTC)


def _ev(ts, action="api", src_ip="10.0.0.9", sev="info", key="k"):
    return {"time": ts, "action": action, "src_ip": src_ip, "severity": sev, "name": "root",
            "event_key": key, "attributes": {"channel": action}}


async def _device(db_engine, tid):
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with f() as s:
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,"
            "status,tags) VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"id": did, "t": tid}); await s.commit()
    return did


async def _ledger(s, tid, did, applied_at):
    await s.execute(text(
        "INSERT INTO config_changes (id,tenant_id,device_id,created_by,kind,operation,target,"
        "baseline_hash,status,applied_at) VALUES (:i,:t,:d,:t,'alias','set','x','h','applied',:a)"),
        {"i": uuid.uuid4(), "t": tid, "d": did, "a": applied_at})


async def test_learns_ip_from_correlated_apply(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        await _ledger(s, ta, did, BASE)               # OPNGMS applied a change at BASE
        await s.commit()
    async with f() as s:
        dev = await s.get(Device, did)
        events = [_ev(BASE + timedelta(seconds=30), src_ip="192.168.6.100")]   # box logged it ~now
        await _attribute_mgmt_ip(s, dev, events)
        assert dev.mgmt_source_ip == "192.168.6.100"   # learned
        assert events[0]["action"] == "opngms"         # reclassified as our own
        assert events[0]["severity"] == "info"


async def test_no_learn_without_correlation(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        dev = await s.get(Device, did)                 # no ledger rows
        events = [_ev(BASE, src_ip="1.2.3.4")]
        await _attribute_mgmt_ip(s, dev, events)
        assert dev.mgmt_source_ip is None
        assert events[0]["action"] == "api"            # unchanged (no false positive)


async def test_ambiguous_batch_does_not_learn(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        await _ledger(s, ta, did, BASE); await s.commit()
    async with f() as s:
        dev = await s.get(Device, did)
        events = [_ev(BASE, src_ip="1.1.1.1", key="a"), _ev(BASE, src_ip="2.2.2.2", key="b")]
        await _attribute_mgmt_ip(s, dev, events)       # two IPs correlate -> ambiguous -> skip
        assert dev.mgmt_source_ip is None


async def test_reclassifies_external_api_as_drift(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        dev = await s.get(Device, did)
        dev.mgmt_source_ip = "192.168.6.100"           # already learned
        ours = _ev(BASE, src_ip="192.168.6.100", key="a")
        theirs = _ev(BASE, src_ip="203.0.113.5", key="b")
        await _attribute_mgmt_ip(s, dev, [ours, theirs])
        assert ours["action"] == "opngms" and ours["severity"] == "info"
        assert theirs["action"] == "api_external" and theirs["severity"] == "medium"
        assert theirs["attributes"]["drift"] is True


async def test_gui_system_events_untouched(db_engine, two_tenants):
    ta, _ = two_tenants
    did = await _device(db_engine, ta)
    f = async_sessionmaker(db_engine, expire_on_commit=False)
    async with f() as s:
        dev = await s.get(Device, did)
        dev.mgmt_source_ip = "192.168.6.100"
        gui = _ev(BASE, action="gui", src_ip="203.0.113.5", sev="medium")
        await _attribute_mgmt_ip(s, dev, [gui])
        assert gui["action"] == "gui" and gui["severity"] == "medium"   # untouched
