"""ReportAggregator.attacker_countries: seeded IDS src_ips -> per-country counts + percentages."""
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import maxminddb
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.geoip import GeoIp
from app.services.reporting.aggregation import ReportAggregator
from tests.factories import make_tenant

FIXTURE = Path(__file__).parent / "fixtures" / "geoip-test.mmdb"


@pytest.fixture
def geoip():
    reader = maxminddb.open_database(str(FIXTURE))
    g = GeoIp(reader)
    yield g
    g.close()


async def _seed(db_engine, tenant_id, rows):
    """rows: list of (device_id, src_ip). Insert IDS events at distinct times."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    async with factory() as s:
        for i, (did, src_ip) in enumerate(rows):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, 'SIG', :src, '8.8.8.8')"
                ),
                {"t": base + timedelta(minutes=i), "d": did, "k": f"k{i}", "tid": tenant_id, "src": src_ip},
            )
        await s.commit()
    return base


async def _make_device(db_engine, tid, name="fw"):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid, "n": name},
        )
        await s.commit()
    return did


async def test_counts_and_pct_sum_to_100(db_engine, geoip):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = await _make_device(db_engine, tid)
    # 3 RU hits (77.88.8.8 x2, 5.255.255.9 x1), 2 US (8.8.8.8), 1 private (10.0.0.1) -> PRIVATE,
    # 1 globally-routable but not-in-db public (45.33.32.156) -> UNKNOWN.
    base = await _seed(db_engine, tid, [
        (did, "77.88.8.8"), (did, "77.88.8.8"), (did, "5.255.255.9"),
        (did, "8.8.8.8"), (did, "8.8.8.8"),
        (did, "10.0.0.1"),
        (did, "45.33.32.156"),
    ])
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        rows = await agg.attacker_countries(
            frm=base - timedelta(hours=1), to=base + timedelta(hours=1), geoip=geoip,
        )
    by_code = {r.code: r.count for r in rows}
    assert by_code == {"RU": 3, "US": 2, "PRIVATE": 1, "UNKNOWN": 1}
    # Sorted by count desc then code.
    assert [r.code for r in rows] == ["RU", "US", "PRIVATE", "UNKNOWN"]
    # Percentages sum to ~100 (total = 7).
    assert round(sum(r.pct for r in rows)) == 100
    assert next(r.pct for r in rows if r.code == "RU") == round(3 / 7 * 100, 1)


async def test_device_filter_and_limit(db_engine, geoip):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    d1 = await _make_device(db_engine, tid, name="fw1")
    d2 = await _make_device(db_engine, tid, name="fw2")
    base = await _seed(db_engine, tid, [
        (d1, "77.88.8.8"),     # RU on d1
        (d2, "8.8.8.8"),       # US on d2
        (d2, "133.11.11.11"),  # JP on d2
    ])
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        frm, to = base - timedelta(hours=1), base + timedelta(hours=1)
        # device-scoped to d2: only US + JP, RU excluded.
        scoped = await agg.attacker_countries(frm=frm, to=to, device_id=d2, geoip=geoip)
        assert {r.code for r in scoped} == {"US", "JP"}
        # limit=1 over the full tenant -> only the top country (one each, tie broken by code -> JP? no:
        # all counts are 1, sort is count desc then code asc -> JP, RU, US; top-1 is JP).
        top1 = await agg.attacker_countries(frm=frm, to=to, limit=1, geoip=geoip)
        assert len(top1) == 1
        assert top1[0].code == "JP"


async def test_empty_range_returns_empty(db_engine, geoip):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = await _make_device(db_engine, tid)
    base = await _seed(db_engine, tid, [(did, "77.88.8.8")])
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        # A range that excludes the single seeded event -> [].
        rows = await agg.attacker_countries(
            frm=base + timedelta(hours=2), to=base + timedelta(hours=3), geoip=geoip,
        )
    assert rows == []
