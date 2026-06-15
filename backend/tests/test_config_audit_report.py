"""Config-changes report section: the aggregator rollup, the build_context wiring + rendered HTML,
and the standard section-toggle precedence — mirrors the reliability section tests.

Seeds `source="config_audit"` events (api/gui/system channels via the `action` column) and asserts the
total + the direct/drift split, the by-channel breakdown, the notable-changes list, that the section
renders when enabled (and is absent when toggled off or when there are no changes), and that
`config_changes` is a registered, default-on section."""
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

def test_config_changes_section_registered_and_default_on():
    assert "config_changes" in SECTION_KEYS
    assert BUILTIN_DEFAULTS["config_changes"] is True


def test_config_changes_section_resolves_like_the_others():
    assert resolve_sections(None, None)["config_changes"] is True
    # tenant settings can turn it off
    assert resolve_sections({"config_changes": False}, None)["config_changes"] is False
    # a per-schedule (per-device) override wins over the tenant default
    assert resolve_sections({"config_changes": True}, {"config_changes": False})["config_changes"] is False


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


async def _config_event(s, tid, did, *, channel, area, actor, severity, key, minute=0):
    """A config_audit event: `action`=channel, `category`=area, `name`=actor, `severity`=info|medium."""
    await s.execute(
        text(
            "INSERT INTO events (time, device_id, source, event_key, tenant_id, category, name, "
            "severity, action) "
            "VALUES (:t, :d, 'config_audit', :k, :tid, :cat, :name, :sev, :act)"
        ),
        {
            "t": BASE + timedelta(minutes=minute), "d": did, "k": key, "tid": tid,
            "cat": area, "name": actor, "sev": severity, "act": channel,
        },
    )


async def _seed(db_engine, tid, did):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # 2 api (info), 1 gui (medium/drift), 1 system (medium/drift).
        await _config_event(s, tid, did, channel="api", area="firewall", actor="root",
                            severity="info", key="c1", minute=0)
        await _config_event(s, tid, did, channel="api", area="monit", actor="root",
                            severity="info", key="c2", minute=5)
        await _config_event(s, tid, did, channel="gui", area="firewall", actor="admin",
                            severity="medium", key="c3", minute=10)
        await _config_event(s, tid, did, channel="system", area="firmware", actor="root",
                            severity="medium", key="c4", minute=15)
        await s.commit()


# ── aggregator: config_audit_rollup ─────────────────────────────────────────

async def test_config_audit_rollup_counts_and_drift_split(db_engine):
    tid, did = await _tenant_and_device(db_engine, name="fw-edge")
    await _seed(db_engine, tid, did)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rollup = await ReportAggregator(s, tid).config_audit_rollup(frm=FRM, to=TO)
    assert rollup.total == 4
    assert rollup.direct == 2          # gui + system are the drift channels
    by_channel = {c.channel: c for c in rollup.by_channel}
    assert by_channel["api"].count == 2 and by_channel["api"].pct == 50.0
    assert by_channel["gui"].count == 1 and by_channel["gui"].pct == 25.0
    assert by_channel["system"].count == 1 and by_channel["system"].pct == 25.0
    # api (2) is the top channel.
    assert rollup.by_channel[0].channel == "api"
    # Notable list: newest first, carrying actor / area / channel / device name.
    assert len(rollup.notable) == 4
    assert rollup.notable[0].channel == "system"      # minute=15 is newest
    assert rollup.notable[0].area == "firmware"
    assert rollup.notable[0].actor == "root"
    assert rollup.notable[0].device == "fw-edge"
    assert rollup.notable[-1].channel == "api"


async def test_config_audit_rollup_is_tenant_isolated_under_rls(db_engine):
    """Under the real opngms_app role (RLS on `events`), tenant A's rollup must exclude tenant B's
    config_audit events — Invariant #1, mirroring the sibling aggregator RLS tests."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ta, tb = uuid.uuid4(), uuid.uuid4()
    da, db_ = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:  # seed as owner (RLS-exempt)
        for tid, slug in [(ta, "cfg-a"), (tb, "cfg-b")]:
            await s.execute(
                text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, :slug, :slug, 'active')"),
                {"id": tid, "slug": slug})
        for tid, did, dn in [(ta, da, "fw-a"), (tb, db_, "fw-b")]:
            await s.execute(
                text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                     "verify_tls, status, tags) "
                     "VALUES (:id, :t, :n, 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"),
                {"id": did, "t": tid, "n": dn})
        await _config_event(s, ta, da, channel="gui", area="firewall", actor="A-ADMIN",
                            severity="medium", key="ka")
        await _config_event(s, tb, db_, channel="gui", area="firewall", actor="B-ADMIN",
                            severity="medium", key="kb")
        await s.commit()

    # Connect as the REAL opngms_app role (RLS active), context on tenant A.
    app_url = make_url(os.environ["TEST_DATABASE_URL"]).set(username=APP_ROLE, password=APP_ROLE_PASSWORD)
    engine = make_engine(app_url.render_as_string(hide_password=False))
    try:
        f2 = async_sessionmaker(engine, expire_on_commit=False)
        async with f2() as s:
            await set_tenant_context(s, ta)
            rollup = await ReportAggregator(s, ta).config_audit_rollup(frm=FRM, to=TO)
        actors = {e.actor for e in rollup.notable}
        assert "A-ADMIN" in actors and "B-ADMIN" not in actors
        assert rollup.total == 1
    finally:
        await engine.dispose()


async def test_config_audit_rollup_empty_range_is_empty(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    await _seed(db_engine, tid, did)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # A range with no events.
        far = BASE + timedelta(days=30)
        rollup = await ReportAggregator(s, tid).config_audit_rollup(frm=far, to=far + timedelta(hours=1))
    assert rollup.total == 0
    assert rollup.direct == 0
    assert rollup.by_channel == []
    assert rollup.notable == []


async def test_config_audit_rollup_is_device_scoped(db_engine):
    """The optional device_id filter restricts the rollup to one device (independently of RLS): a second
    device's config_audit events in the same tenant+range must be excluded when device_id is passed."""
    tid, da = await _tenant_and_device(db_engine, name="fw-a")
    db_ = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(
            text("INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, "
                 "verify_tls, status, tags) "
                 "VALUES (:id, :t, 'fw-b', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"),
            {"id": db_, "t": tid})
        await _config_event(s, tid, da, channel="gui", area="firewall", actor="A-ADMIN",
                            severity="medium", key="da1", minute=0)
        await _config_event(s, tid, db_, channel="api", area="monit", actor="B-ROOT",
                            severity="info", key="db1", minute=5)
        await s.commit()
    async with factory() as s:
        rollup = await ReportAggregator(s, tid).config_audit_rollup(frm=FRM, to=TO, device_id=da)
    assert rollup.total == 1                                   # only device A's event
    assert rollup.direct == 1
    assert {c.channel for c in rollup.by_channel} == {"gui"}
    assert {e.actor for e in rollup.notable} == {"A-ADMIN"}    # B-ROOT excluded


async def test_config_audit_rollup_ignores_non_config_sources(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        # An IDS event in range must NOT show up in the config-changes rollup.
        await s.execute(
            text(
                "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                "VALUES (:t, :d, 'ids', 'k1', :tid, 'ET SCAN', '8.8.8.8')"
            ),
            {"t": BASE, "d": did, "tid": tid},
        )
        await _config_event(s, tid, did, channel="gui", area="firewall", actor="admin",
                            severity="medium", key="c1")
        await s.commit()
    async with factory() as s:
        rollup = await ReportAggregator(s, tid).config_audit_rollup(frm=FRM, to=TO)
    assert rollup.total == 1
    assert rollup.direct == 1
    assert {c.channel for c in rollup.by_channel} == {"gui"}


# ── context builder + rendered HTML ─────────────────────────────────────────

async def test_config_changes_block_built_when_enabled(db_engine):
    tid, did = await _tenant_and_device(db_engine, name="fw-edge")
    await _seed(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["config_changes"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    block = ctx.config_changes
    assert block is not None
    assert block.total == 4
    assert block.direct == 2
    # Channels carry localized labels; api is the top one.
    assert block.channels[0].label == "API"
    labels = {c.label for c in block.channels}
    assert {"API", "WebGUI", "System / console"} <= labels
    # Notable changes list rendered with localized channel + a direct flag for drift rows.
    assert block.changes[0].channel_label == "System / console"
    assert block.changes[0].direct is True
    assert block.changes[0].device == "fw-edge"

    html = render_html(ctx)
    # The section heading (only emitted when the block renders).
    assert "<h2>Config changes</h2>" in html
    assert "API" in html
    assert "firmware" in html
    # The drift channels are emphasized with the `direct` row class.
    assert "config-direct" in html


async def test_config_changes_block_absent_when_section_off(db_engine):
    tid, did = await _tenant_and_device(db_engine)
    await _seed(db_engine, tid, did)
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)  # config_changes OFF
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    assert ctx.config_changes is None
    assert "<h2>Config changes</h2>" not in render_html(ctx)


async def test_config_changes_block_none_when_no_events(db_engine):
    tid, did = await _tenant_and_device(db_engine)  # no config_audit events seeded
    enabled = dict.fromkeys(BUILTIN_DEFAULTS, False)
    enabled["config_changes"] = True
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ctx = await build_context(
            ReportAggregator(s, tid), tenant_name="Acme", timezone_name="UTC", owner=None,
            frm=FRM, to=TO, sections_enabled=enabled, locale="en",
        )
    assert ctx.config_changes is None
