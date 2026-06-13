"""Report attacker-countries section: build_context wiring + rendered HTML, driven by the fixture mmdb.

Seeds IDS events whose src_ips resolve via the vendored tests/fixtures/geoip-test.mmdb (77.88.8.8->RU,
8.8.8.8->US, an RFC1918 -> PRIVATE), then asserts the report-level AttackerCountriesBlock and that the
section renders (and is absent when toggled off / no geoip)."""
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import maxminddb
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.geoip import GeoIp
from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.context import build_context
from app.services.reporting.sections import BUILTIN_DEFAULTS
from app.services.reporting.template import render_html
from tests.factories import make_tenant

FIXTURE = Path(__file__).parent / "fixtures" / "geoip-test.mmdb"

BASE = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
FRM = BASE - timedelta(hours=1)
TO = BASE + timedelta(hours=2)


@pytest.fixture
def geoip():
    reader = maxminddb.open_database(str(FIXTURE))
    g = GeoIp(reader)
    yield g
    g.close()


async def _tenant_and_device(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                "verify_tls, status, tags, firmware_version, edition, firmware_series) "
                "VALUES (:id, :t, 'fw1', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}', "
                "'26.1.9', 'CE', '26.1')"
            ),
            {"id": did, "t": tid},
        )
        await s.commit()
    return tid, did


async def _ids_event(s, tid, did, src_ip, key, minute=0):
    await s.execute(
        text(
            "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
            "VALUES (:t, :d, 'ids', :k, :tid, 'ET SCAN', :ip)"
        ),
        {"t": BASE + timedelta(minutes=minute), "d": did, "tid": tid, "k": key, "ip": src_ip},
    )


async def _seed_events(db_engine, tid, did):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # Two RU hits, one US hit, one private (RFC1918) hit.
        await _ids_event(s, tid, did, "77.88.8.8", "k1", minute=0)
        await _ids_event(s, tid, did, "77.88.8.8", "k2", minute=5)
        await _ids_event(s, tid, did, "8.8.8.8", "k3", minute=10)
        await _ids_event(s, tid, did, "10.0.0.5", "k4", minute=15)
        await s.commit()


async def test_attacker_countries_block_built_with_geoip(db_engine, geoip):
    tid, did = await _tenant_and_device(db_engine)
    await _seed_events(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["attacker_countries"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, geoip=geoip, locale="en",
        )
    block = ctx.attacker_countries
    assert block is not None
    by_name = {r.name: r for r in block.rows}
    # 2 RU + 1 US + 1 private of 4 total events.
    assert by_name["Russia"].count == 2 and by_name["Russia"].pct == 50.0
    assert by_name["United States"].count == 1 and by_name["United States"].pct == 25.0
    assert by_name["Private / internal"].count == 1
    # Sorted by count desc: Russia first.
    assert block.rows[0].name == "Russia"
    # World choropleth is built alongside the table (sentinels carry no geometry -> excluded).
    assert "<svg" in block.map_svg
    assert "choropleth-svg" in block.map_svg


async def test_attacker_countries_rendered_html_contains_title_and_country(db_engine, geoip):
    tid, did = await _tenant_and_device(db_engine)
    await _seed_events(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["attacker_countries"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, geoip=geoip, locale="en",
        )
    html = render_html(ctx)
    assert "Attacker Countries" in html
    assert "Russia" in html
    assert "DB-IP" in html  # attribution line
    assert "choropleth-svg" in html  # world map embedded above the ranked table


async def test_attacker_countries_absent_when_section_off(db_engine, geoip):
    tid, did = await _tenant_and_device(db_engine)
    await _seed_events(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)  # attacker_countries OFF
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, geoip=geoip, locale="en",
        )
    assert ctx.attacker_countries is None
    assert "Attacker Countries" not in render_html(ctx)


async def test_attacker_countries_absent_when_no_geoip(db_engine, monkeypatch):
    """Section ON but no mmdb available (geoip=None, no cached row, auto-fetch off) -> block is None.

    Auto-fetch is patched off so the resolver stays offline/deterministic — no outbound to the release."""
    from types import SimpleNamespace

    import app.services.geoip_provider as provider_mod

    provider_mod.clear_geoip_cache()
    monkeypatch.setattr(
        provider_mod, "get_settings",
        lambda: SimpleNamespace(geoip_auto_fetch=False, geoip_release_base_url=""),
    )
    tid, did = await _tenant_and_device(db_engine)
    await _seed_events(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["attacker_countries"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, geoip=None, locale="en",
        )
    assert ctx.attacker_countries is None
