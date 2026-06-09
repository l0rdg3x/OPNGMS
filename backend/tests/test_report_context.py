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
