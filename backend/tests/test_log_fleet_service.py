import os
import uuid

import httpx
import respx
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.services.log_fleet import (
    fleet_forwarding_counts,
    fleet_log_stats,
    log_fleet_overview,
)


async def _seed_tenant(s, *, slug, enabled, revoked, disabled):
    tid = uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
                    {"i": tid, "n": slug.upper(), "sl": slug})
    await set_tenant_context(s, tid)
    n = 0
    for _ in range(enabled):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
    for _ in range(revoked):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint,revoked_at) "
            "VALUES (:d,:t,false,'s','f',now())"), {"d": did, "t": tid})
    for _ in range(disabled):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,false,'s','f')"), {"d": did, "t": tid})
    return tid


async def test_fleet_forwarding_counts_per_tenant(db_engine):
    # Seed as the owner (db_engine = opngms superuser), where the per-INSERT RLS
    # context drives WITH CHECK on the tenant tables.
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await _seed_tenant(s, slug="acme", enabled=2, revoked=1, disabled=0)
        tb = await _seed_tenant(s, slug="beta", enabled=1, revoked=0, disabled=1)
        await s.commit()
    # Count as the non-superuser app role (opngms_app), where RLS is actually
    # enforced. The owner role is BYPASSRLS, so the per-tenant set_tenant_context
    # loop would otherwise see every tenant's rows. This mirrors production, where
    # the API session connects as opngms_app.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(
        username="opngms_app", password="opngms_app"
    )
    app_engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
        async with app_factory() as s:
            counts = await fleet_forwarding_counts(s)
    finally:
        await app_engine.dispose()
    assert counts[ta]["enabled"] == 2 and counts[ta]["revoked"] == 1 and counts[ta]["disabled"] == 0
    assert counts[ta]["total_devices"] == 3 and counts[ta]["tenant_name"] == "ACME"
    assert counts[tb]["enabled"] == 1 and counts[tb]["disabled"] == 1 and counts[tb]["revoked"] == 0
    assert counts[tb]["total_devices"] == 2


class _S:
    opensearch_url = "http://opensearch:9200"
    log_fleet_terms_size = 10000


_OS = "http://opensearch:9200/opngms-logs-*/_search"


@respx.mock
async def test_fleet_log_stats_maps_buckets():
    route = respx.post(_OS).mock(return_value=httpx.Response(200, json={"aggregations": {"by_tenant": {"buckets": [
        {"key": "tid-a", "doc_count": 9, "last_log": {"value_as_string": "2026-06-01T10:00:00.000Z"},
         "last_24h": {"doc_count": 4}},
    ]}}}))
    stats = await fleet_log_stats(_S())
    assert stats["tid-a"]["volume"] == 4
    assert stats["tid-a"]["last_log_at"] == "2026-06-01T10:00:00.000Z"
    # the terms size comes from the setting (no silent 1000 cap)
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["aggs"]["by_tenant"]["terms"]["size"] == 10000
    # default window is 24h
    assert body["aggs"]["by_tenant"]["aggs"]["last_24h"]["filter"]["range"]["@timestamp"]["gte"] == "now-24h"


@respx.mock
async def test_fleet_log_stats_window_hours_drives_filter():
    route = respx.post(_OS).mock(return_value=httpx.Response(200, json={"aggregations": {"by_tenant": {"buckets": [
        {"key": "tid-a", "doc_count": 9, "last_log": {"value_as_string": "2026-06-01T10:00:00.000Z"},
         "last_24h": {"doc_count": 7}},
    ]}}}))
    stats = await fleet_log_stats(_S(), window_hours=168)
    assert stats["tid-a"]["volume"] == 7
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["aggs"]["by_tenant"]["aggs"]["last_24h"]["filter"]["range"]["@timestamp"]["gte"] == "now-168h"


@respx.mock
async def test_fleet_log_stats_warns_on_truncation(caplog):
    respx.post(_OS).mock(return_value=httpx.Response(200, json={"aggregations": {"by_tenant": {
        "sum_other_doc_count": 42, "buckets": [
            {"key": "tid-a", "doc_count": 9, "last_log": {"value_as_string": "2026-06-01T10:00:00.000Z"},
             "last_24h": {"doc_count": 4}}]}}}))
    import logging
    with caplog.at_level(logging.WARNING):
        await fleet_log_stats(_S())
    assert any("terms agg truncated" in r.message for r in caplog.records)


@respx.mock
async def test_fleet_log_stats_empty_on_error():
    respx.post(_OS).mock(return_value=httpx.Response(503, json={}))
    assert await fleet_log_stats(_S()) == {}


async def test_log_fleet_overview_combines_and_flags_silent(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await _seed_tenant(s, slug="acme", enabled=2, revoked=0, disabled=0)  # forwarding -> will be silent
        await _seed_tenant(s, slug="beta", enabled=0, revoked=0, disabled=1)       # no forwarding
        await s.commit()

    seen: dict = {}

    async def fake_stats(settings, *, window_hours=24):
        seen["window_hours"] = window_hours
        return {}  # OpenSearch returns nothing -> acme has enabled>0 + no last_log -> silent

    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username="opngms_app", password="opngms_app")
    app_engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        app_factory = async_sessionmaker(app_engine, expire_on_commit=False)
        async with app_factory() as s:
            ov = await log_fleet_overview(s, _S(), window_hours=168)
    finally:
        await app_engine.dispose()
    by_id = {r["tenant_id"]: r for r in ov["tenants"]}
    assert by_id[ta]["enabled"] == 2 and by_id[ta]["last_log_at"] is None
    assert "volume" in by_id[ta] and by_id[ta]["volume"] is None
    assert ov["totals"]["tenants_with_forwarding"] == 1
    assert ov["totals"]["silent_tenants"] == 1
    assert ov["totals"]["enabled_devices"] == 2
    assert ov["totals"]["volume"] == 0
    assert seen["window_hours"] == 168  # the window is passed through to fleet_log_stats
