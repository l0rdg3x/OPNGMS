import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.context import build_context
from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.template import render_html
from tests.factories import make_tenant


async def test_build_context_includes_attacks_section(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw-edge', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        for i, name in enumerate(["ET SCAN NMAP", "ET SCAN NMAP", "ET POLICY DNS"]):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, :name, '10.0.0.9', '8.8.4.4')"
                ),
                {"t": base + timedelta(minutes=i), "d": did, "k": f"k{i}", "tid": tid, "name": name},
            )
        await s.commit()

    async with factory() as s:
        agg = ReportAggregator(s, tid)
        ctx = await build_context(
            agg, tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=base - timedelta(hours=1), to=base + timedelta(hours=1),
        )
    assert ctx.toc == ["fw-edge"]
    assert ctx.sections[0].attacks is not None
    html = render_html(ctx)
    assert "ET SCAN NMAP" in html        # ranked table value present
    assert "fw-edge" in html
    assert "<svg" in html                # timeline chart embedded


async def test_build_context_includes_applications_and_web_filter(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw-edge','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        # applications + web_filter default OFF (sample data) since report enrichment; enable them here.
        ctx = await build_context(agg, tenant_name="Acme", timezone_name="UTC", owner=None,
                                  frm=base - timedelta(hours=1), to=base + timedelta(hours=1),
                                  sections_enabled={"applications": True, "web_filter": True})
    sec = ctx.sections[0]
    assert sec.applications is not None and sec.web_filter is not None
    html = render_html(ctx)
    assert "Applications" in html and "Web Filter" in html
    assert "Sample data" in html                 # honesty caption
    assert "threat-high" in html or "threat-low" in html or "threat-guarded" in html


async def test_build_context_includes_web_bandwidth_status(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        await s.commit()
        tid = t.id
    from sqlalchemy import text
    did = uuid.uuid4()
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                             "VALUES (:id,:t,'fw-edge','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, action) "
                             "VALUES (:t,:d,'dns','k1',:tid,'example.org','10.0.0.7','allowed')"), {"t": base, "d": did, "tid": tid})
        await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, action) "
                             "VALUES (:t,:d,'dns','k2',:tid,'tracker.bad','10.0.0.7','blocked')"), {"t": base, "d": did, "tid": tid})
        await s.execute(text("INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                             "VALUES (:t,:d,'ids','k3',:tid,'ET SCAN NMAP','10.0.0.7','8.8.4.4')"), {"t": base, "d": did, "tid": tid})
        for mins, val in ((0, 100.0), (30, 500.0)):
            await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                                 "VALUES (:t,:d,'iface.bytes_in','wan',:tid,:v)"),
                            {"t": base + timedelta(minutes=mins), "d": did, "tid": tid, "v": val})
        await s.execute(text("INSERT INTO metrics (time, device_id, metric, label, tenant_id, value) "
                             "VALUES (:t,:d,'cpu.pct','',:tid,7.0)"), {"t": base + timedelta(minutes=5), "d": did, "tid": tid})
        await s.commit()
    async with factory() as s:
        agg = ReportAggregator(s, tid)
        ctx = await build_context(agg, tenant_name="Acme", timezone_name="UTC", owner=None,
                                  frm=base - timedelta(hours=1), to=base + timedelta(hours=1))
    sec = ctx.sections[0]
    assert sec.web is not None and sec.bandwidth is not None and sec.status is not None
    # Task 2: ctx.t must be populated
    assert ctx.t is not None
    html = render_html(ctx)
    # The rendered report must contain the section heading and the seeded sample hosts. (These are
    # plain substring presence checks on rendered HTML — not URL validation; iterate so static
    # analysis doesn't misread a hostname literal in an `in` test as URL sanitization.)
    for needle in ("Web Activity", "example.org", "tracker.bad"):
        assert needle in html
    assert "Data Usage" in html
    assert "Up/Down Status" in html
    # Task 2: per-chart axis units must appear in the SVG, explanation prose in the template
    assert "DNS lookups" in html          # y_label on the web activity chart
    assert "Attempts</text>" in html      # y_label rendered in the attacks timeline SVG (not the table caption)
    assert "How much data flowed through" in html  # explanation paragraph for Data Usage
    # Task 2: section titles come from ctx.t
    assert "Attacks" in html
    assert "Table of contents" in html
    # Task 2: footer label attributes present in the HTML
    assert 'data-ftz="Report generated for timezone"' in html
    assert 'data-fpage="Page"' in html
