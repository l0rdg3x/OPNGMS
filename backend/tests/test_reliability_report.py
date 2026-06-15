"""Reliability report section: the aggregator rollup, the build_context wiring + rendered HTML, and
the standard section-toggle precedence — mirrors the perimeter / attacker-countries section tests.

Seeds `source="service"` events (reboot / service / disk categories) and asserts the per-category
counts, the notable-events list, that the section renders when enabled (and is absent when toggled off
or when there are no events), and that `reliability` is a registered, default-on section."""
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import make_engine, set_tenant_context
from app.core.db_roles import APP_ROLE, APP_ROLE_PASSWORD
from app.services.reporting.aggregation import ReportAggregator
from app.services.reporting.context import build_context
from app.services.reporting.sections import (
    BUILTIN_DEFAULTS,
    SECTION_KEYS,
    resolve_sections,
)
from app.services.reporting.template import render_html
from tests.factories import make_tenant

BASE = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
FRM = BASE - timedelta(hours=1)
TO = BASE + timedelta(hours=2)


# ── section registration / toggle precedence ────────────────────────────────

def test_reliability_section_registered_and_default_on():
    assert "reliability" in SECTION_KEYS
    assert BUILTIN_DEFAULTS["reliability"] is True


def test_reliability_section_resolves_like_the_others():
    assert resolve_sections(None, None)["reliability"] is True
    # tenant settings can turn it off
    assert resolve_sections({"reliability": False}, None)["reliability"] is False
    # a per-schedule (per-device) override wins over the tenant default
    assert resolve_sections({"reliability": True}, {"reliability": False})["reliability"] is False


# ── DB-backed seeding helpers ───────────────────────────────────────────────

async def _tenant_and_device(db_engine, name="fw1"):
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
                "verify_tls, status, tags) "
                "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid, "n": name},
        )
        await s.commit()
    return tid, did


async def _service_event(s, tid, did, *, category, name, severity, key, minute=0):
    await s.execute(
        text(
            "INSERT INTO events (time, device_id, source, event_key, tenant_id, category, name, severity) "
            "VALUES (:t, :d, 'service', :k, :tid, :cat, :name, :sev)"
        ),
        {
            "t": BASE + timedelta(minutes=minute), "d": did, "k": key, "tid": tid,
            "cat": category, "name": name, "sev": severity,
        },
    )


async def _seed(db_engine, tid, did):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # 2 service crashes, 1 disk warning, 1 reboot.
        await _service_event(s, tid, did, category="service", name="service_crashed", severity="high", key="s1", minute=0)
        await _service_event(s, tid, did, category="service", name="service_restarted", severity="medium", key="s2", minute=5)
        await _service_event(s, tid, did, category="disk", name="filesystem_full", severity="high", key="s3", minute=10)
        await _service_event(s, tid, did, category="reboot", name="reboot", severity="high", key="s4", minute=15)
        await s.commit()


# ── aggregator: reliability_rollup ──────────────────────────────────────────

async def test_reliability_rollup_counts_by_category(db_engine):
    tid, did = await _tenant_and_device(db_engine, name="fw-edge")
    await _seed(db_engine, tid, did)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rollup = await ReportAggregator(s, tid).reliability_rollup(frm=FRM, to=TO)
    assert rollup.total == 4
    by_cat = {c.category: c for c in rollup.by_category}
    assert by_cat["service"].count == 2 and by_cat["service"].pct == 50.0
    assert by_cat["disk"].count == 1 and by_cat["disk"].pct == 25.0
    assert by_cat["reboot"].count == 1 and by_cat["reboot"].pct == 25.0
    # service (2) is the top category.
    assert rollup.by_category[0].category == "service"
    # Notable list: newest first, carrying the device name.
    assert len(rollup.notable) == 4
    assert rollup.notable[0].name == "reboot"        # minute=15 is newest
    assert rollup.notable[0].device == "fw-edge"
    assert rollup.notable[-1].name == "service_crashed"


async def test_reliability_rollup_is_tenant_isolated_under_rls(db_engine):
    """Under the real opngms_app role (RLS on `events`), tenant A's rollup must exclude tenant B's
    service events — Invariant #1, mirroring the sibling aggregator RLS tests."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ta, tb = uuid.uuid4(), uuid.uuid4()
    da, db_ = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:  # seed as owner (RLS-exempt)
        for tid, slug in [(ta, "rel-a"), (tb, "rel-b")]:
            await s.execute(
                text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, :slug, :slug, 'active')"),
                {"id": tid, "slug": slug})
        for tid, did, dn in [(ta, da, "fw-a"), (tb, db_, "fw-b")]:
            await s.execute(
                text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                     "verify_tls, status, tags) "
                     "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"),
                {"id": did, "t": tid, "n": dn})
        await _service_event(s, ta, da, category="reboot", name="A-REBOOT", severity="high", key="ka")
        await _service_event(s, tb, db_, category="reboot", name="B-REBOOT", severity="high", key="kb")
        await s.commit()

    # Connect as the REAL opngms_app role (RLS active), context on tenant A.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        f2 = async_sessionmaker(engine, expire_on_commit=False)
        async with f2() as s:
            await set_tenant_context(s, ta)
            rollup = await ReportAggregator(s, ta).reliability_rollup(frm=FRM, to=TO)
        names = {e.name for e in rollup.notable}
        assert "A-REBOOT" in names and "B-REBOOT" not in names
        assert rollup.total == 1
    finally:
        await engine.dispose()


async def test_reliability_rollup_empty_range_is_empty(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    await _seed(db_engine, tid, did)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # A range with no events.
        far = BASE + timedelta(days=30)
        rollup = await ReportAggregator(s, tid).reliability_rollup(frm=far, to=far + timedelta(hours=1))
    assert rollup.total == 0
    assert rollup.by_category == []
    assert rollup.notable == []


async def test_reliability_rollup_ignores_non_service_sources(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # An IDS event in range must NOT show up in the reliability rollup.
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                "VALUES (:t, :d, 'ids', 'k1', :tid, 'ET SCAN', '8.8.8.8')"
            ),
            {"t": BASE, "d": did, "tid": tid},
        )
        await _service_event(s, tid, did, category="reboot", name="reboot", severity="high", key="s1")
        await s.commit()
    async with factory() as s:
        rollup = await ReportAggregator(s, tid).reliability_rollup(frm=FRM, to=TO)
    assert rollup.total == 1
    assert {c.category for c in rollup.by_category} == {"reboot"}


# ── context builder + rendered HTML ─────────────────────────────────────────

async def test_reliability_block_built_when_enabled(db_engine):
    tid, did = await _tenant_and_device(db_engine, name="fw-edge")
    await _seed(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["reliability"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    block = ctx.reliability
    assert block is not None
    assert block.total == 4
    # Categories carry localized labels; service is the top one.
    assert block.categories[0].label == "Services"
    labels = {c.label for c in block.categories}
    assert {"Services", "Disk / filesystem", "Reboots"} <= labels
    # Notable events list rendered with localized category + severity class.
    assert block.events[0].name == "reboot"
    assert block.events[0].severity == "critical"
    assert block.events[0].device == "fw-edge"

    html = render_html(ctx)
    # The section heading (only emitted when the block renders; the CSS comment also contains the
    # word "Reliability", so assert on the <h2> element specifically).
    assert "<h2>Reliability</h2>" in html
    assert "Services" in html
    assert "service_crashed" in html
    assert 'sev-critical' in html


async def test_reliability_block_absent_when_section_off(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    await _seed(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)  # reliability OFF
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    assert ctx.reliability is None
    # The <h2> heading is only emitted when the section renders (the word also appears in the inlined
    # CSS comment, so the bare substring is not a reliable absence check).
    assert "<h2>Reliability</h2>" not in render_html(ctx)


async def test_reliability_block_none_when_no_events(db_engine):
    tid, did = await _tenant_and_device(db_engine)  # no service events seeded
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["reliability"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    assert ctx.reliability is None
