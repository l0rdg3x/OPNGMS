# Config-change Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface who/what/when changed the OPNsense config (best-effort drift-cause attribution of direct
on-box changes vs API changes) by ingesting the box audit log into a new `config_audit` event source, with
a device timeline tab, an Overview card, a report section, and drift alerts.

**Architecture:** A new event **source `config_audit`** in the source-pluggable pipeline
(`app/services/ingest.py`), fed by a connector capability that POSTs `diagnostics/log/core/audit` and a
pure parser `parse_config_changes` that matches the "changed configuration" audit grammar and attributes a
**channel** (api/gui/system) + area + actor + IP. Direct (gui/system) changes are `severity="medium"` and
raise a deduped ingest-time `Alert(type="config_audit")`. Everything else reuses existing infra (the
`events` hypertable, the keyset events API, the report-section registry) — no schema change, no migration.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy async / pytest + respx (backend); React 19 / TS / Mantine
v9 / Vitest (frontend). Spec: `docs/superpowers/specs/2026-06-15-config-change-audit-design.md`.

**Naming (collision-avoidance — verified against the codebase):** event source `config_audit` and alert
type `config_audit` (NOT `config`/`config_drift`/`config_change` — those name the unrelated template-drift
service `app/services/config_drift.py` and the applied-changes model `app/models/config_change.py`). Report
section key `config_changes` (section keys are their own namespace). Frontend folder `frontend/src/configaudit/`.

---

## File Structure

**PR1 — backend ingest + drift alerts**
- Modify `backend/app/connectors/opnsense/parsers.py` — add `parse_config_changes` + helpers `_classify_channel`, `_change_area`, the `_CONFIG_CHANGE`/`_UUID_RE` regexes.
- Modify `backend/app/connectors/opnsense/profiles.py` — add the `config_changes` capability.
- Modify `backend/app/connectors/opnsense/client.py` — add `get_config_changes`.
- Modify `backend/app/services/alerting.py` — add `raise_config_audit_alerts`.
- Modify `backend/app/services/ingest.py` — `SOURCES += ["config_audit"]`; `_fetch` branch; generalize the alert-collect/route.
- Tests: create `backend/tests/test_parse_config_changes.py`, `backend/tests/test_client_config_changes.py`, `backend/tests/test_config_audit_alerts.py`, `backend/tests/test_ingest_config_audit.py`; update the ingest FakeClients in `test_ingest.py`, `test_ingest_service.py`, `test_service_alerts.py`, `test_opnsense_client.py`, `test_poller_e2e.py` to add `get_config_changes`.

**PR2 — frontend** (outline): `frontend/src/configaudit/{ConfigAuditTab,ConfigAuditCard,configAuditHooks}.tsx` + device-page tab wiring + Overview card wiring + `i18n/en.ts` (+12 locales).

**PR3 — report** (outline): `app/services/reporting/{sections,aggregation,context,i18n,mock_sections}.py` + `templates/report.html.j2` + `report.css`.

**PR4 — docs + live-verify + version** (outline): README, Wiki, CHANGELOG, tag.

---

## PR1 — Backend ingest + drift alerts

Branch: `feat/config-audit-ingest` (already created; the spec commit lives here).

### Task 1: `parse_config_changes` parser

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py` (append after `parse_service_events`)
- Test: `backend/tests/test_parse_config_changes.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_parse_config_changes.py
from app.connectors.opnsense.parsers import parse_config_changes


def _row(line, process="audit", severity="Notice", ts="2026-06-15T19:26:27"):
    return {"timestamp": ts, "process_name": process, "severity": severity, "pid": "1", "line": line}


def _data(rows):
    return {"rows": rows}


# Real api-channel line (remote, carries the source IP). uuid is stripped from change_ref.
_API = (" user root@192.168.6.100 changed configuration to /conf/backup/config-1781551587.0626.xml in "
        "/api/monit/settings/delTest/2f2d1f72-c3bb-4cf6-a716-c88cf2412754 "
        "/api/monit/settings/delTest/2f2d1f72-c3bb-4cf6-a716-c88cf2412754 made changes")
# Real system-channel line (local/script form `(root)`, no IP).
_SYS = (" user (root) changed configuration to /conf/backup/config-1781551620.8666.xml in "
        "/usr/local/opnsense/scripts/firmware/register.php "
        "/usr/local/opnsense/scripts/firmware/register.php made changes")
# Synthesized gui-channel line (legacy WebGUI page, remote).
_GUI = (" user admin@10.0.0.5 changed configuration to /conf/backup/config-1781551999.1.xml in "
        "/firewall_rules.php /firewall_rules.php made changes")


def test_api_change_is_info_not_drift():
    out = parse_config_changes(_data([_row(_API)]))
    assert len(out) == 1
    e = out[0]
    assert e["action"] == "api"          # channel
    assert e["severity"] == "info"
    assert e["category"] == "monit"      # area
    assert e["name"] == "root"           # actor
    assert e["src_ip"] == "192.168.6.100"
    assert e["attributes"]["channel"] == "api"
    assert e["attributes"]["change_ref"] == "/api/monit/settings/delTest"   # trailing uuid stripped
    assert e["attributes"]["backup_file"] == "config-1781551587.0626.xml"


def test_system_change_is_drift_medium_local_actor_no_ip():
    out = parse_config_changes(_data([_row(_SYS)]))
    e = out[0]
    assert e["action"] == "system" and e["severity"] == "medium"
    assert e["name"] == "root" and e["src_ip"] == ""        # local form -> no IP
    assert e["attributes"]["channel"] == "system"


def test_gui_change_is_drift_medium():
    out = parse_config_changes(_data([_row(_GUI)]))
    e = out[0]
    assert e["action"] == "gui" and e["severity"] == "medium"
    assert e["category"] == "firewall" and e["name"] == "admin" and e["src_ip"] == "10.0.0.5"


def test_drops_non_audit_and_non_config_lines():
    rows = [
        _row(_API, process="configd.py"),                         # wrong process -> skip
        _row(" action allowed system.diag.log for user root"),    # audit, but not a config change
        _row(" user root@1.2.3.4 authentication failed"),         # failed-login audit line -> skip
        _row("garbage"),
    ]
    assert parse_config_changes(_data(rows)) == []


def test_event_key_stable_and_dedups_on_backup_file():
    a = parse_config_changes(_data([_row(_API)]))
    b = parse_config_changes(_data([_row(_API)]))
    assert a[0]["event_key"] == b[0]["event_key"]
    # A different save (different backup file) at the same ts -> a different key.
    other = _API.replace("config-1781551587.0626.xml", "config-1781551999.9.xml")
    c = parse_config_changes(_data([_row(other)]))
    assert c[0]["event_key"] != a[0]["event_key"]


def test_fail_safe_on_malformed():
    assert parse_config_changes({"rows": [None, 5, "x"]}) == []
    assert parse_config_changes(None) == []
    assert parse_config_changes([]) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_parse_config_changes.py -q`
Expected: FAIL — `ImportError: cannot import name 'parse_config_changes'`.

- [ ] **Step 3: Implement the parser**

Append to `backend/app/connectors/opnsense/parsers.py` (module already imports `re`, `hashlib`; `_rows`,
`event_key`, `parse_ts` are defined above in the same file):

```python
# Config-change audit lines (process_name="audit") record who changed the config and via which request
# path. Grammar (live-verified, real box 192.168.1.82):
#   user (<user>) changed configuration to <backup> in <path> ...      (local/script change, no IP)
#   user <user>@<ip> changed configuration to <backup> in <path> ...   (remote change, carries source IP)
# Fail-safe: a line that doesn't match is skipped (NEVER raises). The channel rules are a RUNTIME-VERIFY
# starter set (grounded on real api + system samples; the gui form is structurally identical with a .php
# page path) — tuned against the box, same posture as the reliability classifier.
_CONFIG_CHANGE = re.compile(
    r"user\s+(?:\((?P<luser>[^)]+)\)|(?P<ruser>[^@\s]+)@(?P<ip>\d{1,3}(?:\.\d{1,3}){3}))"
    r"\s+changed configuration to\s+(?P<backup>\S+)\s+in\s+(?P<path>\S+)",
    re.IGNORECASE,
)
# A trailing /<uuid> on the request path (e.g. .../delTest/<uuid>) -> stripped for a stable change_ref.
_UUID_TAIL = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _classify_channel(path: str) -> str:
    """Map the request path that wrote the config to a change CHANNEL (best-effort drift attribution).

    /api/...                                -> "api"    (programmatic: OPNGMS, a WebGUI MVC page, or another API client)
    a script under /usr/local/opnsense/...  -> "system" (console / cron / firmware tooling)
    another .php page (legacy WebGUI form)   -> "gui"    (a human in the WebGUI)
    anything else                            -> "system" (best-effort default for local/script writes)
    """
    if path.startswith("/api/"):
        return "api"
    if "/usr/local/opnsense/" in path or "/usr/local/etc/" in path:
        return "system"
    if path.endswith(".php"):
        return "gui"
    return "system"


def _change_area(path: str) -> str:
    """Coarse config area from the request path. /api/firewall/filter/addRule -> 'firewall';
    /firewall_rules.php -> 'firewall'; a script path -> the script stem. 'system' as a last resort."""
    seg = [s for s in path.strip("/").split("/") if s]
    if seg and seg[0] == "api":
        return seg[1] if len(seg) > 1 else "system"
    base = seg[-1] if seg else ""
    base = base.rsplit(".", 1)[0]          # drop the .php extension
    return base.split("_", 1)[0] or "system"


def parse_config_changes(data) -> list[dict]:
    """audit-log rows -> config-change events with best-effort drift attribution.

    Keeps only process_name="audit" lines matching the "changed configuration" grammar; every other line
    (configd.py noise, failed-login lines, garbage) is skipped (fail-safe, never raises). A DIRECT on-box
    change (channel gui/system) is severity "medium" (drift); an API change is "info"."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict) or r.get("process_name") != "audit":
            continue
        m = _CONFIG_CHANGE.search(str(r.get("line", "")))
        if not m:
            continue
        ts = parse_ts(r.get("timestamp"))
        actor = m.group("luser") or m.group("ruser") or ""
        actor_ip = m.group("ip") or ""
        path = m.group("path") or ""
        channel = _classify_channel(path)
        area = _change_area(path)
        change_ref = _UUID_TAIL.sub("", path)
        backup_file = (m.group("backup") or "").rsplit("/", 1)[-1]
        drift = channel in ("gui", "system")
        out.append({
            "time": ts,
            "category": area,
            "src_ip": actor_ip,
            "name": actor,
            "severity": "medium" if drift else "info",
            "action": channel,
            "event_key": event_key(ts, backup_file),
            "attributes": {
                "actor": actor, "actor_ip": actor_ip, "channel": channel, "area": area,
                "change_ref": change_ref, "backup_file": backup_file,
                "message": str(r.get("line", ""))[:500],
            },
        })
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_parse_config_changes.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/opnsense/parsers.py backend/tests/test_parse_config_changes.py
git commit -m "feat(config-audit): parse_config_changes parser with channel attribution"
```

### Task 2: `config_changes` capability + `get_config_changes` connector method

**Files:**
- Modify: `backend/app/connectors/opnsense/profiles.py` (after the `service_events` entry, ~line 107)
- Modify: `backend/app/connectors/opnsense/client.py` (after `get_service_events`, ~line 610)
- Test: `backend/tests/test_client_config_changes.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_client_config_changes.py
import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False, timeout=5)


_LINE = (" user root@192.168.6.100 changed configuration to /conf/backup/config-1.xml in "
         "/api/firewall/filter/addRule /api/firewall/filter/addRule made changes")


@respx.mock
async def test_get_config_changes_posts_audit_and_parses():
    route = respx.post(url__regex=r".*/api/diagnostics/log/core/audit.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"timestamp": "2026-06-15T19:25:38", "process_name": "audit", "severity": "Notice", "line": _LINE},
            {"timestamp": "2026-06-15T19:25:38", "process_name": "configd.py", "severity": "Informational",
             "line": " action allowed system.diag.log for user root"},
        ]}))
    out = await _c().get_config_changes()
    assert route.called
    assert len(out) == 1                        # the configd.py noise row is dropped
    assert out[0]["action"] == "api" and out[0]["category"] == "firewall"


@respx.mock
async def test_get_config_changes_empty_on_no_rows():
    respx.post(url__regex=r".*/api/diagnostics/log/core/audit.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    assert await _c().get_config_changes() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_client_config_changes.py -q`
Expected: FAIL — `AttributeError: 'OpnsenseClient' object has no attribute 'get_config_changes'`.

- [ ] **Step 3: Implement capability + method**

In `backend/app/connectors/opnsense/profiles.py`, after the `service_events` capability entry, add:

```python
    # Config-change audit (who/what/when changed the box config, channel-attributed). Same audit-log
    # endpoint as `auth_failures`; the parser keeps the "changed configuration" line family.
    "config_changes": [_default(_spec(
        _POST("diagnostics/log/core/audit",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_config_changes(r[0])))],
```

In `backend/app/connectors/opnsense/client.py`, after `get_service_events`, add:

```python
    async def get_config_changes(self, since: datetime | None = None) -> list[dict]:
        """Config-change audit events (who/what/when, channel-attributed) from the box audit log.
        `since` is accepted for caller convenience; filtering/dedup happen downstream."""
        return await self._capability("config_changes")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_client_config_changes.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/connectors/opnsense/profiles.py backend/app/connectors/opnsense/client.py backend/tests/test_client_config_changes.py
git commit -m "feat(config-audit): config_changes capability + get_config_changes connector"
```

### Task 3: `raise_config_audit_alerts` alerting helper

**Files:**
- Modify: `backend/app/services/alerting.py` (append after `raise_service_alerts`)
- Test: `backend/tests/test_config_audit_alerts.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_audit_alerts.py
import uuid
from datetime import timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.services.alerting import raise_config_audit_alerts


def _row(name="root", severity="medium"):
    return {"name": name, "severity": severity}


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def test_drift_change_opens_one_deduped_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n1 = await raise_config_audit_alerts(s, device, [_row(), _row()])   # same actor twice -> 1
        await s.commit()
    assert n1 == 1
    async with factory() as s:
        device = await s.get(Device, did)
        n2 = await raise_config_audit_alerts(s, device, [_row()])           # already open -> 0
        await s.commit()
    assert n2 == 0
    async with factory() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM alerts WHERE type='config_audit'"))).scalar_one()
    assert cnt == 1


async def test_api_change_never_alerts(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await raise_config_audit_alerts(s, device, [_row(severity="info")])
        await s.commit()
    assert n == 0
```

> Note: confirm the alerts table name (`alerts` here) matches `test_service_alerts.py`'s query; mirror it.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_config_audit_alerts.py -q`
Expected: FAIL — `ImportError: cannot import name 'raise_config_audit_alerts'`.

- [ ] **Step 3: Implement the helper**

Append to `backend/app/services/alerting.py` (reuses the module's `_open_alerts` + `_open`):

```python
async def raise_config_audit_alerts(session: AsyncSession, device: Device, new_rows: list[dict]) -> int:
    """Open a deduped Alert for each NEW direct (drift) config change — a change made on the box OUTSIDE
    the management API (severity "medium"). Like `raise_service_alerts`, these are point-in-time facts,
    NOT auto-resolved; the dedup on the open (type, label) prevents duplicates when the same change is
    re-seen. `api`-channel changes (severity "info") are never opened. Returns the number of alerts opened."""
    drift = [r for r in new_rows if r.get("severity") == "medium"]
    if not drift:
        return 0
    open_alerts = await _open_alerts(session, device)
    opened = 0
    seen: set[str] = set()
    for r in drift:
        actor = r.get("name", "") or "unknown"
        label = f"Direct config change on {device.name} by {actor}"
        key = ("config_audit", label)
        if key in open_alerts or label in seen:
            continue
        session.add(_open(device, "config_audit", label))
        seen.add(label)
        opened += 1
    if opened:
        await session.flush()
    return opened
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_config_audit_alerts.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/alerting.py backend/tests/test_config_audit_alerts.py
git commit -m "feat(config-audit): raise_config_audit_alerts for direct on-box changes"
```

### Task 4: Wire the `config_audit` source into ingest + route alerts

**Files:**
- Modify: `backend/app/services/ingest.py`
- Test: `backend/tests/test_ingest_config_audit.py` (create)
- Modify (fakes): `backend/tests/test_ingest.py`, `backend/tests/test_ingest_service.py`, `backend/tests/test_service_alerts.py`, `backend/tests/test_opnsense_client.py`, `backend/tests/test_poller_e2e.py` — add `get_config_changes` to each ingest FakeClient.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingest_config_audit.py
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.connectors.opnsense.client import ReachabilityError
from app.models.device import Device
from app.services.ingest import ingest_events


class FakeClient:
    def __init__(self, config=None, fail=False):
        self._config = config or []
        self._fail = fail

    async def get_ids_alerts(self, since=None):
        return []

    async def get_dns_events(self, since=None):
        return []

    async def get_service_events(self, since=None):
        return []

    async def get_config_changes(self, since=None):
        if self._fail:
            raise ReachabilityError("boom")
        return self._config


def _cfg(ts, key, name="admin", channel="gui", severity="medium"):
    return {
        "time": ts, "category": "firewall", "src_ip": "10.0.0.5", "name": name,
        "severity": severity, "action": channel, "event_key": key,
        "attributes": {"actor": name, "channel": channel, "change_ref": "/firewall_rules.php"},
    }


async def _device(db_engine, tenant_id) -> uuid.UUID:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw', 'https://x', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tenant_id},
        )
        await s.commit()
    return did


async def test_ingest_config_audit_writes_events_and_advances_cursor(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(config=[_cfg(now, "c1"), _cfg(now, "c2")]), now)
        await s.commit()
    assert n == 2
    async with factory() as s:
        srcs = (await s.execute(
            text("SELECT source FROM events WHERE source='config_audit'"))).scalars().all()
        cur = (await s.execute(
            text("SELECT last_time FROM ingest_cursors WHERE device_id=:d AND source='config_audit'"),
            {"d": did})).scalar_one()
    assert srcs == ["config_audit", "config_audit"] and cur == now


async def test_ingest_config_audit_drift_raises_alert(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        await ingest_events(s, device, FakeClient(config=[_cfg(now, "c1")]), now)
        await s.commit()
    async with factory() as s:
        cnt = (await s.execute(
            text("SELECT count(*) FROM alerts WHERE type='config_audit' AND device_id=:d"),
            {"d": did})).scalar_one()
    assert cnt == 1


async def test_ingest_config_audit_resilient_to_source_error(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(fail=True), now)   # source raises -> skipped
        await s.commit()
    assert n == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_ingest_config_audit.py -q`
Expected: FAIL — `config_audit` not in `SOURCES` (zero events / no `get_config_changes` call), or
`_fetch` raises `ValueError: unknown source`.

- [ ] **Step 3: Implement the ingest wiring**

In `backend/app/services/ingest.py`:

1. Update the import + module constants:

```python
from app.services.alerting import raise_config_audit_alerts, raise_service_alerts
...
# Active sources.
SOURCES = ["ids", "dns", "service", "config_audit"]
# Sources whose newly-inserted rows feed ingest-time alerting, mapped to their alert raiser.
_ALERTERS = {"service": raise_service_alerts, "config_audit": raise_config_audit_alerts}
```

2. Replace the body of `ingest_events` (the service-only collect/alert) with a generalized collect+route:

```python
async def ingest_events(session: AsyncSession, device: Device, client, now: datetime) -> int:
    """Ingest the events (per source) of a device. Returns the number of new events seen.

    Resilient: an error in one source neither blocks the others nor raises. Idempotent:
    cursor per (device, source) + ON CONFLICT DO NOTHING insert on the dedup PK.

    Side effect: NEW alert-bearing events (a high-severity service event, a direct/drift config change)
    raise a deduped Alert. Best-effort — an alert failure is logged and never aborts the ingest.
    """
    total = 0
    new_rows: dict[str, list[dict]] = {src: [] for src in _ALERTERS}
    for source in SOURCES:
        try:
            total += await _ingest_source(session, device, client, source, new_rows.get(source))
        except OpnsenseError:
            continue  # an unavailable source does not block the others
    for source, rows in new_rows.items():
        if rows:
            try:
                await _ALERTERS[source](session, device, rows)
            except Exception:
                logger.warning("%s alerting failed for device %s", source, device.id, exc_info=True)
    return total
```

3. Add the `_fetch` branch:

```python
    if source == "config_audit":
        return await client.get_config_changes(since)
```

- [ ] **Step 4: Update the ingest FakeClients**

Add this method to the `FakeClient` in each of `tests/test_ingest.py`, `tests/test_ingest_service.py`,
`tests/test_service_alerts.py`, `tests/test_opnsense_client.py`, `tests/test_poller_e2e.py` (any fake that
defines `get_service_events` and is passed to `ingest_events`):

```python
    async def get_config_changes(self, since=None):
        return []
```

> Run `cd backend && grep -rln "get_service_events" tests/` to confirm the exact set before editing.

- [ ] **Step 5: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_ingest_config_audit.py tests/test_ingest.py tests/test_ingest_service.py tests/test_service_alerts.py tests/test_poller_e2e.py tests/test_opnsense_client.py -q`
Expected: PASS (new tests + all previously-passing ingest tests still green).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ingest.py backend/tests/test_ingest_config_audit.py backend/tests/test_ingest.py backend/tests/test_ingest_service.py backend/tests/test_service_alerts.py backend/tests/test_opnsense_client.py backend/tests/test_poller_e2e.py
git commit -m "feat(config-audit): wire config_audit source into ingest + drift alerts"
```

### Task 5: Backend gate (lint + full suite) + open PR1

- [ ] **Step 1: Lint**

Run: `cd backend && ruff check app/`
Expected: no errors (fix any line-length/import issues).

- [ ] **Step 2: Full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: all green (the new source must not regress IDS/DNS/service ingest, the worker, or the events API).

- [ ] **Step 3: Push + open PR1**

```bash
git push -u origin feat/config-audit-ingest
```
Open a PR to `main` titled `feat(config-audit): ingest box config-change audit + drift alerts`. Body: the
spec link + the four backend tasks. Ensure all required CI checks are green; squash-merge.

---

## PR2 — Frontend: device "Config changes" tab + Overview card (outline)

Branch off updated `main` after PR1 merges. Expand into bite-sized tasks at execution time.

**Precedents to mirror exactly:** `frontend/src/reliability/{ReliabilityTab,ReliabilityCard,reliabilityHooks}.tsx`
and how they are wired into the device detail page + Overview page + `i18n`.

1. **`frontend/src/configaudit/configAuditHooks.ts`** — `useConfigAuditEvents(deviceId)` (infinite query
   over `GET /api/tenants/{tenant_id}/events` with `source: "config_audit"`, keyset `after`, PAGE_SIZE 50)
   and `useConfigAuditSummary()` (`GET /events/top` with `field: "action"`, `source: "config_audit"`,
   `from` = now-24h — the **channel** breakdown; `action` IS in the `/events/top` allow-list, unlike
   `category`). Mirror `reliabilityHooks.ts` (memoize the `from` range once per mount).
2. **`frontend/src/configaudit/ConfigAuditTab.tsx`** — keyset-paginated table; columns **time · area
   (`category`) · actor (`name`) · IP (`src_ip`) · channel (`action`) · change (`attributes.change_ref`)**,
   with a **"Direct"** badge (yellow) when `action ∈ {gui, system}` (drift). Reuse the `attr()` helper +
   load-more button pattern from `ReliabilityTab`.
3. **`frontend/src/configaudit/ConfigAuditCard.tsx`** — Overview card "Direct config changes (24h)":
   render the channel rows from `useConfigAuditSummary()`, emphasizing the gui/system (direct) totals.
4. **Wire** the tab into the device detail page (next to the Reliability tab) and the card into the
   Overview page, following exactly how `ReliabilityTab`/`ReliabilityCard` are imported and placed.
5. **i18n** — add a `configAudit` key group to `frontend/src/i18n/en.ts` (title, subtitle, last24h,
   loading, empty, loadError, loadMore, column labels time/area/actor/ip/channel/change, `direct` badge,
   `channels` map {api, gui, system, unknown}), then **mirror the same keys across all 12 sibling locales**
   (`it es fr de pt nl ru ar zh zhTW ja`) — compiler-enforced parity.
6. **Gate:** `cd frontend && npm run gen:api` (the events API is unchanged, but regenerate to be safe),
   then `npm run build` (the gate), `npm run lint`, `npm test`. Add a Vitest for the tab (mock the events
   API; assert the Direct badge renders for a gui row) mirroring the reliability tab test under
   `frontend/src/reliability/__tests__/`.

---

## PR3 — Report: `config_changes` section (outline)

Branch off updated `main` after PR2 merges. **Precedent to mirror exactly:** the `reliability` report
section across `app/services/reporting/{sections,aggregation,context,i18n,mock_sections}.py` +
`templates/report.html.j2` + `report.css` (see `aggregation.py:554 reliability_rollup` and
`mock_sections.py:85 reliability_block`).

1. **`sections.py`** — add `"config_changes"` to `SECTION_KEYS` and `BUILTIN_DEFAULTS` (default **on**).
2. **`aggregation.py`** — a `config_audit_rollup(frm, to, device_ids)` (mirror `reliability_rollup`):
   tenant-scoped, parameterized SQL over `events WHERE source='config_audit'` in `[frm, to]` — totals,
   the **direct/drift** count (`severity='medium'`), a by-channel breakdown, and a notable-changes list
   (time · device · actor · area · channel). Add the `ConfigAuditBlock` / row dataclasses next to the
   reliability ones (~`aggregation.py:114`).
3. **`context.py`** — build the `config_changes` block when the section is enabled for the device set
   (mirror the reliability wiring; honor `report↔retention` range guard + the standard toggle precedence).
4. **`mock_sections.py`** — a deterministic `config_audit_block(t)` sample for the demo/sample report.
5. **`i18n.py`** — `config_changes_title` / `_explain` / `_direct` / `_by_channel` / `_notable` keys for
   **all** report locales (mirror the `reliability_*` block in every locale section).
6. **`templates/report.html.j2` + `report.css`** — a `config_changes` section block mirroring the
   `reliability` section (a by-channel table + a notable-changes table, drift rows emphasized).
7. **Tests:** the rollup returns expected counts/drift split on seeded events and degrades to empty;
   toggle precedence holds; the section renders. Run `cd backend && python -m pytest -q` + `ruff check app/`.

---

## PR4 — Docs + live-verify + version (outline)

Branch off updated `main` after PR3 merges.

1. **Live-verify (box 192.168.1.82):** apply a change via the connector (`/api/...`) → confirm it appears
   with `channel=api`, `severity=info`, correct area; if feasible, make a console/GUI change and confirm
   `channel ∈ {system, gui}` + a `config_audit` alert. Capture/confirm the real-row classification.
2. **CHANGELOG.md** — a new version entry (next minor, e.g. **0.16.0**) describing the config-change audit
   source, the device tab, the Overview card, the report section, and the drift alerts; note the honest
   best-effort attribution + the management-IP follow-up.
3. **README.md** — a "Config changes" feature bullet + the status-table row (mirror the Reliability rows).
4. **Wiki** (`OPNGMS.wiki`) — document the `config_audit` source on the Architecture page (next to the
   service/reliability source) and the per-device tab on the relevant operator page.
5. **Tag** the version after merge; update the memory + the milestone open-items list; sweep merged branches.

---

## Self-review (plan vs spec)

- **Spec coverage:** source `config_audit` + parser (T1) ✓; capability + connector (T2) ✓; drift alerts
  (T3) ✓; ingest wiring + resilience + dedup (T4) ✓; timeline tab + Overview card + i18n (PR2) ✓; report
  section (PR3) ✓; docs + live-verify + version (PR4) ✓; best-effort channel attribution + honest limit
  (parser + PR4 notes) ✓; no schema change/migration (storage in `events`) ✓.
- **Naming consistency:** source `config_audit`, alert type `config_audit`, capability `config_changes`,
  method `get_config_changes`, parser `parse_config_changes`, helper `raise_config_audit_alerts`, report
  key `config_changes`, frontend `configAudit`/`configaudit/` — used identically across all tasks.
- **Type/shape consistency:** the parser emits the generic event dict (`time, category, src_ip, name,
  severity, action, event_key, attributes`) that `_normalize` already maps; the alert collect uses the
  `{name, severity}` rows returned by `_ingest_source`'s `RETURNING name, severity` — `name`=actor,
  `severity`=info/medium — matching `raise_config_audit_alerts`.
- **Placeholder scan:** none — every code step has complete code; the PR2-4 outlines are intentionally
  structured (to be expanded into bite-sized tasks at execution time, per the spec's build phases).
