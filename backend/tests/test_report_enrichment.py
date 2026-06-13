"""Report-enrichment: aggregation accessors, section-aware build_context, and rendered HTML presence."""
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.context import build_context
from app.services.reporting.sections import BUILTIN_DEFAULTS
from app.services.reporting.template import render_html
from tests.factories import make_tenant

BASE = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
FRM = BASE - timedelta(hours=1)
TO = BASE + timedelta(hours=2)


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


async def _metric(s, tid, did, metric, value, label="", minute=0):
    await s.execute(
        text(
            "INSERT INTO metrics (time, device_id, tenant_id, metric, label, value) "
            "VALUES (:t, :d, :tid, :m, :l, :v)"
        ),
        {"t": BASE + timedelta(minutes=minute), "d": did, "tid": tid, "m": metric, "l": label, "v": value},
    )


async def test_health_summary_avg_and_peak(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await _metric(s, tid, did, "cpu.pct", 10.0, minute=0)
        await _metric(s, tid, did, "cpu.pct", 30.0, minute=10)
        await _metric(s, tid, did, "mem.pct", 50.0, minute=0)
        await _metric(s, tid, did, "disk.pct", 80.0, minute=0)
        await s.commit()
    async with factory() as s:
        hs = await ReportAggregator(s, tid).health_summary(frm=FRM, to=TO, bucket="1 hour", device_id=did)
    assert hs.has_data is True
    assert hs.cpu.avg == 20.0 and hs.cpu.peak == 30.0
    assert hs.mem.avg == 50.0 and hs.disk.peak == 80.0
    assert hs.cpu_series  # at least one bucket


async def test_gateway_quality_and_vpn(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await _metric(s, tid, did, "gateway.rtt_ms", 10.0, label="WAN", minute=0)
        await _metric(s, tid, did, "gateway.rtt_ms", 20.0, label="WAN", minute=10)
        await _metric(s, tid, did, "gateway.loss_pct", 0.0, label="WAN", minute=0)
        await _metric(s, tid, did, "gateway.up", 1.0, label="WAN", minute=0)
        await _metric(s, tid, did, "gateway.up", 0.0, label="WAN", minute=10)
        await _metric(s, tid, did, "vpn.up", 1.0, label="site-a", minute=0)
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        gws = await agg.gateway_quality(frm=FRM, to=TO, device_id=did)
        vpns = await agg.vpn_status(frm=FRM, to=TO, device_id=did)
    assert len(gws) == 1
    assert gws[0].name == "WAN" and gws[0].rtt_ms == 15.0 and gws[0].up_pct == 50.0
    assert vpns[0].name == "site-a" and vpns[0].up_pct == 100.0


async def test_alerts_and_config_changes_in_range(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO alerts (id, tenant_id, device_id, type, label, severity, opened_at, resolved_at) "
                "VALUES (:id, :tid, :d, 'device_unreachable', 'WAN', 'critical', :o, :r)"
            ),
            {"id": uuid.uuid4(), "tid": tid, "d": did, "o": BASE, "r": BASE + timedelta(minutes=30)},
        )
        await s.execute(
            text(
                "INSERT INTO config_changes (id, tenant_id, device_id, created_by, kind, operation, target, "
                "baseline_hash, status, applied_at) "
                "VALUES (:id, :tid, :d, :u, 'alias', 'set', 'MyAlias', 'h', 'applied', :a)"
            ),
            {"id": uuid.uuid4(), "tid": tid, "d": did, "u": uuid.uuid4(), "a": BASE},
        )
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        alerts = await agg.alerts_in_range(frm=FRM, to=TO, device_id=did)
        count, changes = await agg.config_changes_in_range(frm=FRM, to=TO, device_id=did)
    assert len(alerts) == 1 and alerts[0].severity == "critical"
    assert count == 1 and changes[0].kind == "alias" and changes[0].target == "MyAlias"


async def test_kpis(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await _metric(s, tid, did, "cpu.pct", 10.0, minute=0)
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name) "
                "VALUES (:t, :d, 'ids', 'k1', :tid, 'ET SCAN')"
            ),
            {"t": BASE, "d": did, "tid": tid},
        )
        await s.commit()
    async with factory() as s:
        k = await ReportAggregator(s, tid).kpis(frm=FRM, to=TO, bucket="1 hour")
    assert k.devices_total == 1 and k.devices_online == 1
    assert k.attacks_blocked == 1


async def test_build_context_respects_section_toggles(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    # All sections OFF except health -> only health block built, no summary, no attacks.
    only_health = dict.fromkeys(BUILTIN_DEFAULTS, False)
    only_health["health"] = True
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=only_health,
        )
    assert ctx.summary is None
    assert len(ctx.sections) == 1
    sec = ctx.sections[0]
    assert sec.health is not None
    assert sec.attacks is None and sec.alerts_wan is None and sec.firmware_config is None


async def test_rendered_html_includes_enabled_and_excludes_disabled(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["summary"] = True
    enabled["firmware_config"] = True
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    html = render_html(ctx)
    assert "Executive Summary" in html       # summary ON (report-level)
    assert "Firmware &amp; Configuration" in html  # firmware_config ON (autoescaped &)
    assert "Device Health" not in html        # health OFF
    assert "Up/Down Status" not in html       # status OFF
