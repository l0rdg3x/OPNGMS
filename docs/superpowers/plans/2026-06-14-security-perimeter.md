# Security / Perimeter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface failed logins to the box + firewall-blocked attacker IPs (with GeoIP country) in the
per-tenant UI (Overview cards + a `/perimeter` page) and the PDF report (two per-device-toggled sections).

**Architecture:** Two new version-aware `OpnsenseClient` capabilities (auth-failure lines from
`POST diagnostics/log/core/audit`; structured `firewall/log` blocks) feed a per-device ingest that
UPSERTs a **bounded `perimeter_attacker` rollup** (per device/kind/src_ip) — not per-packet events —
reusing the existing `IngestCursor`. Aggregation endpoints + report sections read the rollup; GeoIP is
resolved at query time via the existing provider.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy async / Alembic / TimescaleDB / ARQ; React 19 /
Mantine v9 / Vite.

Reference spec: `docs/superpowers/specs/2026-06-14-security-perimeter-design.md`.

---

## File structure (PR1 — backend ingest)

- Create `backend/app/models/perimeter_attacker.py` — the rollup model.
- Create `backend/alembic/versions/00XX_perimeter_attacker.py` — table + RLS migration.
- Modify `backend/app/connectors/opnsense/parsers.py` — add `parse_firewall_blocks`, `parse_auth_failures`.
- Modify `backend/app/connectors/opnsense/profiles.py` — add `firewall_blocks`, `auth_failures` capabilities.
- Modify `backend/app/connectors/opnsense/client.py` — add `get_firewall_blocks`, `get_auth_failures`.
- Create `backend/app/services/perimeter.py` — `ingest_perimeter(session, device, client, now)` + retention.
- Modify `backend/app/worker.py` — call `ingest_perimeter` from `ingest_device_events`; add a retention cron.
- Tests: `backend/tests/test_perimeter_parsers.py`, `test_perimeter_ingest.py`, `test_perimeter_rls.py`.

---

## PR1 — Backend ingest  (branch `feat/perimeter-ingest`)

### Task 1: `perimeter_attacker` rollup model + migration

**Files:**
- Create: `backend/app/models/perimeter_attacker.py`
- Create: `backend/alembic/versions/<rev>_perimeter_attacker.py`
- Test: `backend/tests/test_perimeter_rls.py`

- [ ] **Step 1: Write the model**

```python
# backend/app/models/perimeter_attacker.py
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PerimeterAttacker(Base):
    """Bounded per-(device, kind, src_ip) rollup of perimeter threat observations.

    kind: 'login_failed' (failed box logins) | 'firewall_block' (blocked traffic). Tenant-scoped with
    a fail-closed RLS policy. The worker writes as owner; the API reads as opngms_app under the
    per-request tenant context. NOT per-packet — one row per distinct attacker IP per kind per device.
    """

    __tablename__ = "perimeter_attacker"

    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True
    )
    kind: Mapped[str] = mapped_column(Text, primary_key=True)
    src_ip: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    count: Mapped[int] = mapped_column(BigInteger, default=0)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
```

- [ ] **Step 2: Write the migration** — mirror an existing tenant-scoped table migration (e.g. the
  `events`/`devices` RLS migration). The migration must: `create_table` with the columns above; add the
  fail-closed RLS policy + `FORCE ROW LEVEL SECURITY` using the same helper SQL the other tenant tables
  use (`enable_rls_statements()` pattern in `backend/app/db_roles.py` / migration 0003 idiom — read a
  recent tenant-table migration like the `events` one and copy its policy block verbatim, swapping the
  table name); grant the `opngms_app` role `SELECT, INSERT, UPDATE, DELETE`. Add an index on
  `(tenant_id, kind, last_seen DESC)` to back the ranked queries.

  Generate the revision: `cd backend && alembic revision -m "perimeter_attacker"` then fill the
  `upgrade()` body (forward-only; no real `downgrade`). Find the down_revision from
  `alembic heads`.

- [ ] **Step 3: Write the failing RLS test** (`backend/tests/test_perimeter_rls.py`)

```python
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.models.perimeter_attacker import PerimeterAttacker


async def test_perimeter_attacker_is_tenant_isolated(two_tenants, db_engine):
    ta, tb = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    did = uuid.uuid4()
    async with factory() as s:  # owner: seed a device + a rollup row for tenant A
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": ta})
        s.add(PerimeterAttacker(device_id=did, kind="firewall_block", src_ip="1.2.3.4", tenant_id=ta,
                                count=3, first_seen=datetime.now(UTC), last_seen=datetime.now(UTC), detail={}))
        await s.commit()
    # As opngms_app under tenant B's context -> must NOT see tenant A's row.
    from tests.conftest import app_role_sessionmaker  # or build via app_url like other RLS tests
    # (Use the existing RLS-session helper the other test_*_rls.py files use.)
```

  Model the RLS assertion on an existing `test_*_rls.py` (e.g. `test_config_rls_api.py` /
  `test_events` RLS test) — connect as the `opngms_app` login role, `set_tenant_context(tb)`, and assert
  a `SELECT` returns 0 rows; under `ta` it returns 1.

- [ ] **Step 4: Run it** — `cd backend && python -m pytest tests/test_perimeter_rls.py -q` (needs the
  migration applied to the test schema; the `db_engine` fixture builds the schema from `Base.metadata`
  + `enable_rls_statements()`, so ensure the model is imported and the RLS policy is in
  `enable_rls_statements()` or the conftest schema build). Expected: FAIL then PASS once RLS is wired.

- [ ] **Step 5: Commit** — `feat(perimeter): perimeter_attacker rollup model + RLS migration`.

### Task 2: `parse_firewall_blocks` (structured — the easy one)

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py`
- Test: `backend/tests/test_perimeter_parsers.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_perimeter_parsers.py
from app.connectors.opnsense.parsers import parse_firewall_blocks


def test_parse_firewall_blocks_keeps_blocks_only():
    rows = [
        {"action": "block", "src": "203.0.113.9", "dst": "10.0.0.1", "srcport": "5555",
         "dstport": "23", "interface": "igb0", "protoname": "tcp",
         "__timestamp__": "2026-06-14T10:00:00", "__digest__": "abc123"},
        {"action": "pass", "src": "10.0.0.5", "dst": "8.8.8.8", "__timestamp__": "2026-06-14T10:00:01",
         "__digest__": "def456"},  # pass -> dropped
    ]
    out = parse_firewall_blocks(rows)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "203.0.113.9"
    assert e["event_key"] == "abc123"          # __digest__ is the dedup key
    assert e["attributes"]["dstport"] == "23"
    assert e["attributes"]["interface"] == "igb0"
    assert e["time"] is not None
```

- [ ] **Step 2: Run it** — `pytest tests/test_perimeter_parsers.py::test_parse_firewall_blocks_keeps_blocks_only -v` → FAIL (not defined).

- [ ] **Step 3: Implement** in `parsers.py` (mirror `parse_ids_rows`; reuse `parse_ts`, `_rows`):

```python
def parse_firewall_blocks(data) -> list[dict]:
    """diagnostics/firewall/log rows -> normalized firewall-block observations (action=block only).

    Structured source; __digest__ is the per-line dedup key. Defensive toward missing keys."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict) or str(r.get("action", "")).lower() != "block":
            continue
        src = r.get("src", "")
        if not src:
            continue
        ts = parse_ts(r.get("__timestamp__") or r.get("timestamp"))
        key = r.get("__digest__") or event_key(ts, src, r.get("dst", ""), r.get("dstport", ""))
        out.append({
            "time": ts, "src_ip": src,
            "name": str(r.get("dstport", "")),       # the targeted port
            "event_key": str(key),
            "attributes": {k: r.get(k) for k in ("dst", "dstport", "srcport", "interface", "protoname")},
        })
    return out
```

  Note: `_rows(data, "rows")` must also accept a bare top-level list (the firewall log returns a JSON
  array, not `{"rows": [...]}`). Verify `_rows` handles a list; if not, pass `data` through a
  `data if isinstance(data, list) else data.get("rows", [])` guard inside the parser.

- [ ] **Step 4: Run it** → PASS.

- [ ] **Step 5: Commit** — `feat(perimeter): parse_firewall_blocks (blocks only, digest dedup)`.

### Task 3: `parse_auth_failures` (audit-log text lines)

**Files:**
- Modify: `backend/app/connectors/opnsense/parsers.py`
- Test: `backend/tests/test_perimeter_parsers.py`

> **Real-box verification (do this during the task):** the exact failed-login line wording must be
> confirmed against the real box (192.168.1.82) or OPNsense source. The verified shapes from the box:
> rows are `{timestamp, severity, process_name, pid, line}`, `process_name == "audit"` for auth events;
> a session line is `"Session timed out for user 'X' from: <ip>"`. OPNsense logs a failed GUI login as
> an `audit`-process line naming the user + remote address. Write the regex against the
> `user '<name>' ... from[:]? <ip>` family and a failure marker (`failed`/`could not`/`Wrong`/`denied`);
> a single deliberate failed login on the box (with operator go-ahead) confirms the exact string. The
> parser MUST fail safe: an unrecognized line is skipped.

- [ ] **Step 1: Write the failing test**

```python
from app.connectors.opnsense.parsers import parse_auth_failures


def test_parse_auth_failures_extracts_user_and_ip():
    rows = {"rows": [
        {"timestamp": "2026-06-14T10:00:00", "process_name": "audit",
         "line": " authentication failed for user 'admin' from 203.0.113.7"},
        {"timestamp": "2026-06-14T10:00:01", "process_name": "audit",
         "line": " Successful login for user 'root' from 10.0.0.2"},     # success -> dropped
        {"timestamp": "2026-06-14T10:00:02", "process_name": "configd.py",
         "line": " action allowed system.diag.log for user root"},       # not auth -> dropped
    ]}
    out = parse_auth_failures(rows)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "203.0.113.7"
    assert e["name"] == "admin"             # username attempted
    assert e["time"] is not None
    assert e["event_key"]                   # stable per (time, ip, user)
```

- [ ] **Step 2: Run it** → FAIL.

- [ ] **Step 3: Implement** (regex, fail-safe):

```python
import re

_AUTH_FAIL = re.compile(
    r"(?:authentication failed|could not authenticate|wrong (?:password|username)|login failed|denied)"
    r".*?user '(?P<user>[^']*)'.*?(?:from[: ]+)(?P<ip>\d{1,3}(?:\.\d{1,3}){3})",
    re.IGNORECASE,
)


def parse_auth_failures(data) -> list[dict]:
    """diagnostics/log/core/audit rows -> failed-login observations (process_name=audit only).

    Text-log parsing; fail-safe (an unrecognized line is skipped). Extracts attempted username +
    source IP."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict) or r.get("process_name") != "audit":
            continue
        m = _AUTH_FAIL.search(str(r.get("line", "")))
        if not m:
            continue
        ts = parse_ts(r.get("timestamp"))
        ip, user = m.group("ip"), m.group("user")
        out.append({
            "time": ts, "src_ip": ip, "name": user,
            "event_key": event_key(ts, ip, user),
            "attributes": {"username": user, "severity": r.get("severity", "")},
        })
    return out
```

- [ ] **Step 4: Run it** → PASS. Then verify against the real box (read-only POST to the audit log;
  adjust the regex if the live failure string differs) and add a regression test with the exact line.

- [ ] **Step 5: Commit** — `feat(perimeter): parse_auth_failures (audit log, fail-safe regex)`.

### Task 4: OpnsenseClient capabilities + methods

**Files:**
- Modify: `backend/app/connectors/opnsense/profiles.py`
- Modify: `backend/app/connectors/opnsense/client.py`
- Test: `backend/tests/test_opnsense_client.py` (respx-mocked, like the IDS/DNS client tests)

- [ ] **Step 1: Write the failing test** — mock the two endpoints with respx and assert the client
  methods return the parsed rows (mirror the existing `test_get_ids_alerts`-style tests; check how those
  are written first).

```python
@respx.mock
async def test_get_firewall_blocks(...):
    respx.get(f"{BASE}/api/diagnostics/firewall/log").mock(
        return_value=httpx.Response(200, json=[{"action": "block", "src": "1.1.1.1",
            "__timestamp__": "2026-06-14T10:00:00", "__digest__": "d1", "dstport": "22"}]))
    out = await OpnsenseClient(BASE, "k", "s").get_firewall_blocks()
    assert out[0]["src_ip"] == "1.1.1.1"
```

- [ ] **Step 2: Run it** → FAIL.

- [ ] **Step 3: Implement** — in `profiles.py` add to `CAPABILITIES`:

```python
    "firewall_blocks": [_default(_spec(
        _GET("diagnostics/firewall/log"),
        combine=lambda r: parsers.parse_firewall_blocks(r[0])))],
    "auth_failures": [_default(_spec(
        _POST("diagnostics/log/core/audit",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_auth_failures(r[0])))],
```

  In `client.py` add (mirroring `get_ids_alerts`):

```python
    async def get_firewall_blocks(self, since: datetime | None = None) -> list[dict]:
        return await self._capability("firewall_blocks")

    async def get_auth_failures(self, since: datetime | None = None) -> list[dict]:
        return await self._capability("auth_failures")
```

- [ ] **Step 4: Run it** → PASS. (Verify the firewall log path/method against the real box — it
  responded to both GET and POST; GET is simpler.)

- [ ] **Step 5: Commit** — `feat(perimeter): firewall_blocks + auth_failures client capabilities`.

### Task 5: `ingest_perimeter` aggregator + cursor

**Files:**
- Create: `backend/app/services/perimeter.py`
- Test: `backend/tests/test_perimeter_ingest.py`

- [ ] **Step 1: Write the failing test** — a fake client returning rows for both kinds; run
  `ingest_perimeter`; assert the rollup has one row per src_ip with the right count + merged detail, and
  that a second run with overlapping rows increments count (UPSERT) and advances the cursor.

```python
import uuid
from datetime import UTC, datetime
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.device import Device
from app.models.perimeter_attacker import PerimeterAttacker
from app.services.perimeter import ingest_perimeter


class FakeClient:
    def __init__(self, fw, au): self._fw, self._au = fw, au
    async def get_firewall_blocks(self, since=None): return self._fw
    async def get_auth_failures(self, since=None): return self._au


async def test_ingest_perimeter_rolls_up_by_ip(db_engine, two_tenants):
    ta, _ = two_tenants
    did = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text("INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": ta})
        await s.commit()
        dev = await s.get(Device, did)
        fw = [{"time": datetime(2026,6,14,10,tzinfo=UTC), "src_ip": "1.1.1.1", "name": "23",
               "event_key": "d1", "attributes": {"dstport": "23", "interface": "igb0"}},
              {"time": datetime(2026,6,14,10,1,tzinfo=UTC), "src_ip": "1.1.1.1", "name": "80",
               "event_key": "d2", "attributes": {"dstport": "80", "interface": "igb0"}}]
        client = FakeClient(fw, [])
        await ingest_perimeter(s, dev, client, now=datetime.now(UTC))
        await s.commit()
        row = (await s.execute(select(PerimeterAttacker).where(PerimeterAttacker.src_ip == "1.1.1.1"))).scalar_one()
    assert row.kind == "firewall_block" and row.count == 2
    assert set(row.detail.get("top_ports", [])) >= {"23", "80"}
```

- [ ] **Step 2: Run it** → FAIL.

- [ ] **Step 3: Implement** `app/services/perimeter.py`:

```python
"""Perimeter ingest: poll failed logins + firewall blocks, UPSERT a bounded per-(device,kind,src_ip)
rollup. Reuses IngestCursor; resilient (one source's error never blocks the other)."""
import contextlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseError
from app.models.device import Device
from app.models.ingest_cursor import IngestCursor
from app.models.perimeter_attacker import PerimeterAttacker

# (capability getter, kind, detail-merge) per source.
_KINDS = [("firewall_block", "get_firewall_blocks"), ("login_failed", "get_auth_failures")]
RETENTION_DAYS = 30


async def ingest_perimeter(session: AsyncSession, device: Device, client, now: datetime) -> int:
    total = 0
    for kind, getter in _KINDS:
        with contextlib.suppress(OpnsenseError):
            total += await _ingest_kind(session, device, client, kind, getter)
    return total


async def _ingest_kind(session, device, client, kind, getter) -> int:
    cursor = await session.get(IngestCursor, (device.id, kind))
    since = cursor.last_time if cursor else None
    rows = await getattr(client, getter)()
    rows = [r for r in rows if r.get("time") and (since is None or r["time"] > since)]
    if not rows:
        return 0
    # group by src_ip
    by_ip: dict[str, list] = {}
    for r in rows:
        by_ip.setdefault(r["src_ip"], []).append(r)
    for ip, group in by_ip.items():
        await _upsert(session, device, kind, ip, group)
    new_max = max(r["time"] for r in rows)
    await _advance(session, device.id, kind, new_max)
    return len(rows)


def _detail(kind: str, group: list) -> dict:
    if kind == "firewall_block":
        ports = sorted({str(r["attributes"].get("dstport")) for r in group if r["attributes"].get("dstport")})
        return {"top_ports": ports[:10]}
    users = sorted({r["attributes"].get("username") for r in group if r["attributes"].get("username")})
    return {"usernames": users[:10], "last_username": group[-1]["attributes"].get("username")}


async def _upsert(session, device, kind, ip, group):
    times = [r["time"] for r in group]
    stmt = (
        pg_insert(PerimeterAttacker)
        .values(device_id=device.id, tenant_id=device.tenant_id, kind=kind, src_ip=ip,
                count=len(group), first_seen=min(times), last_seen=max(times), detail=_detail(kind, group))
        .on_conflict_do_update(
            index_elements=["device_id", "kind", "src_ip"],
            set_={
                "count": PerimeterAttacker.count + len(group),
                "last_seen": func.greatest(PerimeterAttacker.last_seen, max(times)),
                # detail merge: union the JSONB arrays (kept simple; recompute top-N in the query layer)
                "detail": _detail(kind, group),
            },
        )
    )
    await session.execute(stmt)
```

  (Import `func` from sqlalchemy. The detail "merge on conflict" keeps only the latest batch's
  top-N for simplicity — acceptable since the query layer ranks; document this. If exact cumulative
  union is wanted, store raw and aggregate in the query — out of scope for v1.)

- [ ] **Step 4: Run it** → PASS. Add a second test: a second `ingest_perimeter` with new rows for the
  same IP increments `count` and advances the cursor (no double-count of already-seen times).

- [ ] **Step 5: Commit** — `feat(perimeter): ingest_perimeter rollup aggregator + cursor`.

### Task 6: Worker wiring + retention sweep

**Files:**
- Modify: `backend/app/worker.py`
- Modify: `backend/app/services/perimeter.py` (add `purge_perimeter`)
- Test: `backend/tests/test_perimeter_ingest.py` (retention) + `test_worker_config.py` (cron registered)

- [ ] **Step 1: Write the failing test** — `purge_perimeter(session, now)` deletes rows with
  `last_seen < now - RETENTION_DAYS`; assert an old row is deleted, a fresh one kept. Plus a
  `test_worker` assertion that the perimeter ingest runs inside `ingest_device_events`.

- [ ] **Step 2: Run it** → FAIL.

- [ ] **Step 3: Implement** — in `perimeter.py`:

```python
async def purge_perimeter(session: AsyncSession, now: datetime) -> int:
    cutoff = now - timedelta(days=RETENTION_DAYS)
    res = await session.execute(
        PerimeterAttacker.__table__.delete().where(PerimeterAttacker.last_seen < cutoff))
    return res.rowcount or 0
```

  In `worker.py`: inside `ingest_device_events` (after `ingest_events(...)`), add
  `await ingest_perimeter(session, device, client, now=now)` (same client, same resilience). Add a
  `purge_perimeter_job` worker function + a daily cron in `WorkerSettings.cron_jobs`
  (mirror `sweep_orphaned_actions` / `cleanup_expired_sessions`), reading the owner session.

- [ ] **Step 4: Run it** → PASS. Run the full suite + ruff.

- [ ] **Step 5: Commit** — `feat(perimeter): wire perimeter ingest into the events cron + retention sweep`.

### PR1 wrap-up
- [ ] `cd backend && python -m pytest -q && ruff check app/` (full suite, green).
- [ ] Push, open PR, green CI, **security-review the diff** (new outbound capability + a new
  tenant-scoped table → RLS), squash-merge.

---

## PR2 — Backend aggregation API  (branch `feat/perimeter-api`)

> Expand into bite-sized TDD tasks at execution time. Patterns: mirror `attacker-countries`
> (`app/api/monitoring.py:110` + `ReportAggregator.attacker_countries` in
> `app/services/reporting/aggregation.py`, which resolves `src_ip -> country` via `geoip_provider`).

- **Task A:** `ReportAggregator.perimeter_top(kind, frm, to, geoip, limit, device_ids=None)` → ranked
  `[{src_ip, country, count, last_seen, label}]` from `perimeter_attacker` (label = `detail.last_username`
  for login / top port for firewall). Reuse the GeoIP resolution helper used by `attacker_countries`.
- **Task B:** `GET …/{tenant}/perimeter/summary?kind=&window=` (top N, backs the Overview cards) and
  `GET …/{tenant}/perimeter/attackers?kind=&window=&page=` (paginated full list), both
  `require_tenant(Action.DEVICE_VIEW)`-gated like the monitoring endpoints; tenant-scoped (RLS).
  Schemas in `app/schemas/`. Tests: RBAC, tenant isolation, ranking, GeoIP enrichment, empty state.
- **Task C:** full suite + ruff; push, PR, green CI, squash-merge.

---

## PR3 — Frontend: Overview cards + `/perimeter` page  (branch `feat/perimeter-ui`)

> Expand at execution time.

- **Task A:** `npm run gen:api` for the new endpoint types; `src/perimeter/perimeterHooks.ts`
  (react-query, typed client) mirroring `overview/AttackerCountriesCard` data hooks.
- **Task B:** Overview cards `FailedLoginsCard` + `FirewallBlocksCard` (top N: IP + country + count +
  username/port), each linking to `/perimeter`. Add to `OverviewPage.tsx`.
- **Task C:** `PerimeterPage.tsx` at route `/perimeter` + a nav item "Perimeter" in `AppShell.tsx`
  (distinct from the account-security `/security/*` items): a window selector + tables (failed logins
  by IP/user, firewall blocks by IP/port).
- **Task D:** i18n — add a `perimeter` section to `en.ts`, mirror into all 12 locales (parallel
  per-locale translation subagents; `tsc -b` enforces parity).
- **Task E:** Vitest (msw) for cards + page; run the **build gate** `npm run build`, `npm test`,
  `npm run lint`. Push, PR, green CI, squash-merge.

---

## PR4 — Reports: per-device toggle + two sections  (branch `feat/perimeter-reports`)

> Expand at execution time.

- **Task A:** `devices.report_perimeter` JSONB column (default
  `{"failed_logins": true, "firewall_blocks": true}`) + migration; expose it on the device read/update
  API; two switches on the device detail page (+ i18n in 12 locales). Tests.
- **Task B:** `ReportAggregator` perimeter methods accept the enabled-device set; `build_context`
  (`app/services/reporting/context.py`) builds two new blocks (`failed_logins`, `firewall_blocks`)
  from the devices whose toggle is on; render them in `report.html.j2` in the attacker-countries style.
  Tests: a device with the toggle off is excluded; the section omitted when no device is enabled.
- **Task C:** refresh the demo report (`/tmp/gen_demo_reports.py`) + the preview PNGs; full suite + ruff
  + build gate. Push, PR, green CI, squash-merge. Then **tag a version** + CHANGELOG (milestone complete).

---

## Self-review notes
- **Spec coverage:** ingest (PR1) ✓; rollup-not-events storage ✓; GeoIP at query (PR2) ✓; Overview cards
  + `/perimeter` page + i18n (PR3) ✓; per-device report toggle + two sections (PR4) ✓; retention ✓;
  invariants (SSRF client, RLS, no secrets) ✓.
- **Real-box caveat:** the auth-failure regex is verified/adjusted against the live box in PR1 Task 3.
- **Naming consistency:** `kind ∈ {'login_failed','firewall_block'}` used identically in the model,
  cursor `source`, ingest `_KINDS`, and the API `kind` param.
- **Decoupling:** `perimeter_attacker` is separate from `events`; the existing attacker-countries widget
  is untouched.
