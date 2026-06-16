# Config-audit Management-IP Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-learn OPNGMS's management source IP (correlated with its own applied-change ledger) and reclassify `api` config-audit changes by actor IP: OPNGMS's IP → `opngms` (expected), any other IP → `api_external` (drift, alerts).

**Architecture:** A new nullable `Device.mgmt_source_ip` + an ingest-time `_attribute_mgmt_ip` that learns the IP and mutates the config-audit source's parsed events before they are stored. The pure parser is unchanged; alerts ride the existing `severity=="medium"` path.

**Tech Stack:** Python 3.14 / SQLAlchemy / Alembic / pytest. Spec: `docs/superpowers/specs/2026-06-16-config-audit-mgmt-ip-design.md`.

---

## PR1 — Backend: learn + reclassify

### Task 1: `Device.mgmt_source_ip` column + migration

**Files:**
- Modify: `backend/app/models/device.py`
- Create: `backend/migrations/versions/0042_device_mgmt_source_ip.py`
- Test: `backend/tests/test_device_mgmt_ip_migration.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_device_mgmt_ip_migration.py
from sqlalchemy import text


async def test_devices_has_mgmt_source_ip_column(db_engine):
    async with db_engine.begin() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='devices' AND column_name='mgmt_source_ip'"))).scalars().all()
    assert cols == ["mgmt_source_ip"]
```

- [ ] **Step 2: Run to verify it fails** — `cd backend && python -m pytest tests/test_device_mgmt_ip_migration.py -q` (FAIL: column absent).

- [ ] **Step 3: Add the model field + migration**

In `app/models/device.py`, after `firmware_series`:

```python
    # The source IP the box sees OPNGMS connecting from, auto-learned from the config-audit log
    # correlated with OPNGMS's own applied-change ledger. None until learned; drives api-change
    # attribution (opngms vs api_external drift). See app/services/ingest.py::_attribute_mgmt_ip.
    mgmt_source_ip: Mapped[str | None] = mapped_column(default=None)
```

Create `app/migrations/versions/0042_device_mgmt_source_ip.py`:

```python
"""device.mgmt_source_ip — auto-learned management IP for config-audit attribution"""

import sqlalchemy as sa
from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("mgmt_source_ip", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "mgmt_source_ip")
```

- [ ] **Step 4: Run to verify it passes** — the test DB schema is built from metadata (`create_all`), so the model field makes the column appear. `python -m pytest tests/test_device_mgmt_ip_migration.py -q` (PASS).

- [ ] **Step 5: Commit** — `git add app/models/device.py migrations/versions/0042_device_mgmt_source_ip.py tests/test_device_mgmt_ip_migration.py && git commit -m "feat(config-audit): device.mgmt_source_ip column + migration 0042"`

### Task 2: `_learn_mgmt_ip` + `_attribute_mgmt_ip` (TDD)

**Files:**
- Modify: `backend/app/services/ingest.py`
- Test: `backend/tests/test_config_audit_mgmt_ip.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_config_audit_mgmt_ip.py
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
```

- [ ] **Step 2: Run to verify it fails** — `ImportError: cannot import name '_attribute_mgmt_ip'`.

- [ ] **Step 3: Implement**

In `app/services/ingest.py`, add the import `from datetime import timedelta` (alongside `datetime`) and:

```python
_MGMT_CORR_WINDOW = timedelta(minutes=3)


async def _learn_mgmt_ip(session: AsyncSession, device: Device, api_events: list[dict]) -> str | None:
    """OPNGMS's source IP if an api-event correlates (within the window) with an OPNGMS-applied change on
    this device, and the correlated events agree on a single IP; else None (no/ambiguous correlation)."""
    times = [e["time"] for e in api_events]
    lo, hi = min(times) - _MGMT_CORR_WINDOW, max(times) + _MGMT_CORR_WINDOW
    applied = (await session.execute(
        text("SELECT applied_at FROM config_changes WHERE device_id = :d AND status = 'applied' "
             "AND applied_at BETWEEN :lo AND :hi"),
        {"d": device.id, "lo": lo, "hi": hi})).scalars().all()
    if not applied:
        return None
    w = _MGMT_CORR_WINDOW.total_seconds()
    ips = {e["src_ip"] for e in api_events
           if any(abs((e["time"] - a).total_seconds()) <= w for a in applied)}
    return next(iter(ips)) if len(ips) == 1 else None


async def _attribute_mgmt_ip(session: AsyncSession, device: Device, events: list[dict]) -> None:
    """Auto-learn OPNGMS's management IP and reclassify api-channel config-audit changes by actor IP:
    OPNGMS's IP -> 'opngms' (expected), any other IP -> 'api_external' (drift, severity medium -> alerts).
    Mutates `events` in place; a no-op until the IP is learned. Runs before the events are stored."""
    api_events = [e for e in events if e.get("action") == "api" and e.get("src_ip")]
    if not api_events:
        return
    learned = await _learn_mgmt_ip(session, device, api_events)
    if learned and device.mgmt_source_ip != learned:
        device.mgmt_source_ip = learned
    mgmt = device.mgmt_source_ip
    if not mgmt:
        return
    for e in api_events:
        attrs = dict(e.get("attributes", {}))
        if e["src_ip"] == mgmt:
            e["action"] = "opngms"
            attrs["origin"] = "opngms"
        else:
            e["action"] = "api_external"
            e["severity"] = "medium"
            attrs["origin"] = "api_external"
            attrs["drift"] = True
        e["attributes"] = attrs
```

The required `text` import already exists? — `ingest.py` uses `pg_insert`; add `from sqlalchemy import text` if not present.

- [ ] **Step 4: Run to verify it passes** — `python -m pytest tests/test_config_audit_mgmt_ip.py -q` (5 tests PASS).

- [ ] **Step 5: Commit** — `git commit -m "feat(config-audit): _attribute_mgmt_ip learn + reclassify (opngms / api_external)"`

### Task 3: Wire into `ingest_events`

**Files:** Modify `backend/app/services/ingest.py`; Test: add to `backend/tests/test_ingest_config_audit.py`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_ingest_config_audit.py`)

```python
async def test_ingest_config_audit_attributes_and_alerts_external(db_engine, two_tenants):
    """An api change from a non-management IP becomes api_external (drift) and raises an alert; the device
    must already have a learned mgmt_source_ip."""
    import uuid as _uuid
    from datetime import datetime as _dt, timezone as _tz
    from sqlalchemy import text as _t
    from sqlalchemy.ext.asyncio import async_sessionmaker as _f
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = _dt(2026, 6, 16, 12, 0, tzinfo=_tz.utc)
    factory = _f(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(_t("UPDATE devices SET mgmt_source_ip='10.0.0.1' WHERE id=:d"), {"d": did})
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
        action = (await s.execute(_t(
            "SELECT action FROM events WHERE source='config_audit' AND device_id=:d"),
            {"d": did})).scalar_one()
        alerts = (await s.execute(_t(
            "SELECT count(*) FROM alerts WHERE type='config_audit' AND device_id=:d"),
            {"d": did})).scalar_one()
    assert action == "api_external" and alerts == 1
```

> Note: confirm `test_ingest_config_audit.py`'s `FakeClient` accepts `config=` and exposes the 4 source methods; reuse its existing `_device` helper.

- [ ] **Step 2: Run to verify it fails** (the event stores as `api`, no alert).

- [ ] **Step 3: Wire it in** — in `ingest_events`, inside the store loop, before `_store_source`:

```python
    for source, raw in zip(SOURCES, raws, strict=True):
        if isinstance(raw, OpnsenseError):
            continue
        if isinstance(raw, BaseException):
            raise raw
        if source == "config_audit":
            await _attribute_mgmt_ip(session, device, raw)
        total += await _store_source(session, device, source, raw, sinces[source], new_rows.get(source))
```

- [ ] **Step 4: Run to verify it passes** + the existing config-audit ingest tests still green.

- [ ] **Step 5: Commit** — `git commit -m "feat(config-audit): attribute mgmt IP during ingest (api_external drift alerts)"`

### Task 4: Backend gate + PR

- [ ] `cd backend && ruff check app/ && python -m pytest -q` → all green. Push `feat/config-audit-mgmt-ip`; open PR1.

---

## PR2 — Frontend + report channel labels (outline)

Branch off updated `main` after PR1. The new `action` values `opngms` / `api_external` flow through the
existing timeline/card/report (which key on `action`); add labels + extend the Direct badge.

1. **Frontend** (`frontend/src/configaudit/`): `configAuditHooks.ts` `isDirectChannel` → also true for
   `api_external` (it is drift); `channelLabel` falls back to raw, but add `opngms` + `api_external` to the
   `channels` i18n map. `en.ts` `configAudit.channels`: `opngms: "OPNGMS"`, `apiExternal: "External API"`
   (key the map by the raw action string `api_external`). Mirror across all 12 locales. `npm run build` +
   `npm test` (add a tab test: an `api_external` row shows the Direct badge + the External-API label).
2. **Report i18n** (`app/services/reporting/i18n.py` → `locales/<code>.py`): add `config_channel_opngms`
   + `config_channel_api_external` to every locale; `context.py::_config_channel_label` maps them; the
   report's direct/drift count + the `_config_changes_block` `direct` predicate include `api_external`
   (channels in `{"gui","system","api_external"}`). Add a report test.
3. Gate: `npm run build` + `pytest -q` + `ruff`.

---

## PR3 — Docs + live-verify + version (outline)

1. **Live-verify (box 192.168.1.82):** apply a change via OPNGMS → after the next ingest the device's
   `mgmt_source_ip` is learned and that change shows `opngms`; (if feasible) a WebGUI/other-IP api change
   shows `api_external` + alerts.
2. **CHANGELOG / README / Wiki:** document management-IP attribution (auto-learned) under the config-audit
   feature. **Tag the version** (this is folded into the v0.17.0 release the user requested — see the
   milestone-finalization steps: README+Wiki, screenshots, demo reports, tag v0.17.0).

---

## Self-review (plan vs spec)

- **Spec coverage:** model + migration (T1) ✓; learn correlated-with-ledger + conservative-ambiguity (T2
  `_learn_mgmt_ip` + tests) ✓; reclassify opngms/api_external + severity (T2) ✓; ingest wiring + alert via
  existing medium path (T3) ✓; behaviour-preserving until learned (T2 `test_no_learn...`) ✓; frontend +
  report labels + Direct-badge extension (PR2) ✓; live-verify + docs + version (PR3) ✓.
- **Placeholder scan:** none — PR1 has complete code; PR2/PR3 are structured outlines per the spec's phases.
- **Type/name consistency:** `mgmt_source_ip`, `_attribute_mgmt_ip`, `_learn_mgmt_ip`, action values
  `opngms`/`api_external`, `_MGMT_CORR_WINDOW` used identically across tasks; alerts reuse `severity=="medium"`.
