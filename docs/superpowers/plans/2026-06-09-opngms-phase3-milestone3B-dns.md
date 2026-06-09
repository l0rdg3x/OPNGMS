# OPNGMS — Phase 3 / Milestone 3B: DNS Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the **DNS** source (Unbound queries → "visited sites") to event ingest, reusing the 3A framework (hypertable `events`, cursor per `(device, source)`, dedup, worker).

**Architecture:** 3A made the ingest generic on `source`. 3B adds only: a connector method `get_dns_events(since)` that normalises DNS queries, and activation of the `"dns"` source in the `ingest_events` service (`SOURCES` list + `_fetch` dispatch). Storage, cursor, dedup, RLS, cron, and job remain unchanged.

**Tech Stack:** Python 3.12+, SQLAlchemy 2.0 async, TimescaleDB, ARQ, pytest + respx.

---

## Context for the implementer (read before starting)

Backend codebase at `/home/l0rdg3x/coding/OPNGMS/backend`. 3A is already on `main`.

- **Connector** (`app/connectors/opnsense/client.py`): `get_ids_alerts(since)` (lines ~167-201) is the model to replicate for `get_dns_events`. Uses `self._get(path)` (single HTTP boundary + SSRF), `self._parse_ts(...)` (always returns a tz-aware `datetime`), `self._event_key(ts, *parts)` (discriminating hash when no source id is available). `datetime`/`timezone`/`hashlib` are already imported.
- **Ingest service** (`app/services/ingest.py`):
  - `SOURCES = ["ids"]` → becomes `["ids", "dns"]`.
  - `_fetch(client, source, since)` dispatches by source (currently only `ids`); add the `dns` branch.
  - `_normalize(device, source, r)` is **already generic**: reads `time, category, src_ip, dst_ip, name, severity, action, event_key, attributes` from the connector dict. NOT to be modified (the DNS dict must have these keys).
  - `ingest_events` is resilient per-source (`except OpnsenseError: continue`): a DNS source error does not block IDS and vice versa.
- **Event model** (`app/models/event.py`): `Event` with dedup PK `(time, device_id, source, event_key)`. For DNS: `source="dns"`, `category="query"`, `src_ip=client_ip`, `name=domain`, `action=allowed|blocked`.
- **Ingest tests** (`tests/test_ingest.py`): contains a `FakeClient` with ONLY `get_ids_alerts`. ⚠️ **Adding `"dns"` to `SOURCES`, `ingest_events` will call `client.get_dns_events` even in existing tests** → without updating `FakeClient` you get `AttributeError` (NOT an `OpnsenseError`, so not caught) and the 3 existing 3A tests break. The `FakeClient` and its call sites MUST be updated (Task 2).
- **Connector tests**: `tests/test_connector_ids.py` is the model for `tests/test_connector_dns.py` (respx).

**Test command** (from `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Current suite: **138 green tests**.

⚠️ **OPNsense DNS endpoint TO BE VERIFIED — the most uncertain source** (3A debt): the API exposure of OPNsense (Unbound) DNS logs is not confirmed. `get_dns_events` is written against a *plausible* payload and tested with respx; if no usable endpoint exists on the real device, DNS collection will remain mocked until then. **NOT a blocker** for storage/dedup/API: the abstraction holds and ON CONFLICT dedup is the safety net. No new schema/migrations in 3B.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/connectors/opnsense/client.py` | `get_dns_events(since)` | Modify |
| `tests/test_connector_dns.py` | respx for `get_dns_events` | Create |
| `app/services/ingest.py` | `"dns"` in `SOURCES` + `_fetch` dispatch | Modify |
| `tests/test_ingest.py` | multi-source `FakeClient` + DNS/both/resilience tests | Modify |

---

## Task 1: `get_dns_events` connector

**Files:**
- Modify: `app/connectors/opnsense/client.py`
- Create: `tests/test_connector_dns.py`

- [ ] **Step 1: Write the respx test (fails)**

Create `tests/test_connector_dns.py` (mirror of `test_connector_ids.py`). *Plausible* DNS payload (Unbound queries):
```python
import httpx
import respx

from app.connectors.opnsense.client import OpnsenseClient


@respx.mock
async def test_get_dns_events_normalizes():
    payload = {
        "rows": [
            {
                "timestamp": "2026-06-09T12:00:00+00:00",
                "client": "10.0.0.20",
                "domain": "example.com",
                "action": "allowed",
                "query_id": "q1",
            }
        ]
    }
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_dns_events(since=None)
    assert len(out) == 1
    e = out[0]
    assert e["src_ip"] == "10.0.0.20"
    assert e["name"] == "example.com"       # domain = "visited site"
    assert e["action"] == "allowed"
    assert e["category"] == "query"
    assert e["dst_ip"] == ""
    assert e["severity"] == ""
    assert e["event_key"]                    # source id or hash
    assert e["time"].tzinfo is not None      # tz-aware


@respx.mock
async def test_get_dns_events_key_variants_and_empty():
    # key variants + fallback hash + empty payload
    payload = {
        "queries": [
            {"time": "2026-06-09T13:00:00Z", "client_ip": "10.0.0.21", "query": "blocked.test", "action": "blocked"}
        ]
    }
    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json=payload)
    )
    client = OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)
    out = await client.get_dns_events()
    assert out[0]["src_ip"] == "10.0.0.21"
    assert out[0]["name"] == "blocked.test"
    assert out[0]["action"] == "blocked"
    assert out[0]["event_key"]  # content hash (no id)

    respx.get(url__regex=r".*/api/unbound/diagnostics/queries.*").mock(
        return_value=httpx.Response(200, json={})
    )
    assert await client.get_dns_events() == []
```

- [ ] **Step 2: Run and verify the failure**

Run: `... pytest tests/test_connector_dns.py -v` → FAIL (`get_dns_events` does not exist).

- [ ] **Step 3: Implement `get_dns_events`**

In `app/connectors/opnsense/client.py`, add after `get_ids_alerts` (and before `_parse_ts`):
```python
    async def get_dns_events(self, since: datetime | None = None) -> list[dict]:
        """Normalised DNS queries (Unbound) -> "visited sites".

        NOTE: endpoint `unbound/diagnostics/queries` and payload format TO BE VERIFIED
        on a real OPNsense device — this is the most uncertain source (see 3A debt). Defensive
        against key variants. `since` is a hint: fine filtering and dedup happen downstream.
        """
        data = await self._get("unbound/diagnostics/queries")
        out: list[dict] = []
        for r in data.get("rows", data.get("queries", [])):
            ts = self._parse_ts(r.get("timestamp", r.get("time")))
            client_ip = r.get("client") or r.get("client_ip") or ""
            domain = r.get("domain") or r.get("query") or r.get("name") or ""
            action = r.get("action", "")  # allowed | blocked
            # event_key: stable id if present, otherwise content hash.
            key = r.get("query_id") or r.get("id") or r.get("_id") or self._event_key(
                ts, client_ip, domain, action
            )
            out.append({
                "time": ts,
                "category": "query",
                "src_ip": client_ip,
                "dst_ip": "",
                "name": domain,
                "severity": "",
                "action": action,
                "event_key": str(key),
                "attributes": r,
            })
        return out
```

- [ ] **Step 4: Run and verify the pass**

Run: `... pytest tests/test_connector_dns.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add app/connectors/opnsense/client.py tests/test_connector_dns.py
git commit -m "feat(backend): connector get_dns_events (Unbound DNS query normalisation)"
```

---

## Task 2: Activate the `dns` source in the ingest

**Files:**
- Modify: `app/services/ingest.py`
- Modify: `tests/test_ingest.py`

- [ ] **Step 1: Update `FakeClient` + write DNS tests (they fail)**

In `tests/test_ingest.py`, **replace** the existing `FakeClient` with a multi-source version (maintains compatibility with the first positional argument `alerts`):
```python
class FakeClient:
    def __init__(self, alerts=None, dns=None, fail_ids=False, fail_dns=False):
        self._alerts = alerts or []
        self._dns = dns or []
        self._fail_ids = fail_ids
        self._fail_dns = fail_dns

    async def get_ids_alerts(self, since=None):
        if self._fail_ids:
            raise ReachabilityError("boom")
        return self._alerts

    async def get_dns_events(self, since=None):
        if self._fail_dns:
            raise ReachabilityError("boom")
        return self._dns
```
**Update the existing call site** in `test_ingest_resilient_to_source_error`: `FakeClient([], fail=True)` → `FakeClient(fail_ids=True)` (the old `fail` kwarg no longer exists). The other call sites (`FakeClient([_alert(...)])`) remain valid.

Add a `_dns` helper and the new tests at the end of the file:
```python
def _dns(ts, key, client="10.0.0.20", domain="example.com", action="allowed"):
    return {
        "time": ts, "category": "query", "src_ip": client, "dst_ip": "",
        "name": domain, "severity": "", "action": action, "event_key": key, "attributes": {},
    }


async def test_ingest_dns_writes_events(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(dns=[_dns(now, "d1")]), now)
        await s.commit()
    assert n == 1
    async with factory() as s:
        src = (await s.execute(text("SELECT source FROM events WHERE source='dns'"))).scalars().all()
    assert src == ["dns"]


async def test_ingest_both_sources_in_one_run(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        n = await ingest_events(s, device, FakeClient(alerts=[_alert(now, "k1")], dns=[_dns(now, "d1")]), now)
        await s.commit()
    assert n == 2  # 1 ids + 1 dns
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events ORDER BY source"))).scalars().all()
    assert srcs == ["dns", "ids"]


async def test_ingest_dns_fails_ids_succeeds(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    did = await _device(db_engine, tenant_a)
    now = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        device = await s.get(Device, did)
        # DNS raises, IDS succeeds: per-source resilience ensures IDS is still ingested
        n = await ingest_events(s, device, FakeClient(alerts=[_alert(now, "k1")], fail_dns=True), now)
        await s.commit()
    assert n == 1
    async with factory() as s:
        srcs = (await s.execute(text("SELECT source FROM events"))).scalars().all()
    assert srcs == ["ids"]
```

- [ ] **Step 2: Run and verify the failure**

Run: `... pytest tests/test_ingest.py -v`
Expected: the new DNS tests FAIL (the `dns` source is not in `SOURCES`, so no dns events are written). The 3 existing 3A tests must still PASS (FakeClient now has `get_dns_events` returning `[]` by default → the new `dns` iteration breaks nothing; the resilience test uses `fail_ids=True`).

- [ ] **Step 3: Activate the `dns` source**

In `app/services/ingest.py`:
```python
SOURCES = ["ids", "dns"]
```
and in `_fetch`, add the `dns` branch:
```python
async def _fetch(client, source: str, since):
    if source == "ids":
        return await client.get_ids_alerts(since)
    if source == "dns":
        return await client.get_dns_events(since)
    raise ValueError(f"unknown source: {source}")
```

- [ ] **Step 4: Run and verify the pass**

Run: `... pytest tests/test_ingest.py -v` → all PASS (3 existing + 3 new). Then the full suite is green.

- [ ] **Step 5: Commit**
```bash
git add app/services/ingest.py tests/test_ingest.py
git commit -m "feat(backend): activate DNS source in ingest (SOURCES + _fetch dispatch)"
```

---

## Task 3: Technical debt

- [ ] **Step 1: Record 3B debt**

Append to this plan:
```markdown
## Technical debt (3B)

- **DNS endpoint TO BE VERIFIED (most uncertain source)**: `unbound/diagnostics/queries` and the payload
  are plausible but not confirmed. If OPNsense does not expose DNS logs via API in a usable way, consider
  an alternative source (Zenarmor, periodic export) or switching to syslog push for DNS.
- **`since` not honoured for DNS either** (same as IDS): client-side filter + dedup; refine with real device.
- **No `dst_ip`/resolver for DNS** (`dst_ip=""`): if the upstream resolver is needed for reports,
  map it from attributes.
- **Identical DNS queries at the same instant** (same client+domain+action, no id): collapsed by dedup
  — acceptable, but for "hits per site" counts could undercount identical closely-spaced queries.
  Consider a counter or source id when available.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase3-milestone3B-dns.md
git commit -m "docs: technical debt milestone 3B"
```

---

## Definition of "done" (3B)
- The `get_dns_events` connector normalises DNS queries (respx).
- The `"dns"` source is active in the ingest: DNS events land in `events` (`source='dns'`), with the same idempotency/dedup as IDS.
- IDS and DNS coexist in a single run; an error in one source does not block the other (tested).
- Green suite (no 3A tests broken by the updated `FakeClient`).

---

## Technical debt (3B) — consolidated from reviews

- **DNS endpoint TO BE VERIFIED (most uncertain source)**: `unbound/diagnostics/queries` and the payload
  are plausible but not confirmed. If OPNsense does not expose DNS logs via API in a usable way, consider
  an alternative source (Zenarmor, periodic export) or syslog push for DNS.
- **`since` not honoured for DNS either** (same as IDS): client-side filter + dedup; refine with real device.
- **No `dst_ip`/resolver for DNS** (`dst_ip=""`): if the upstream resolver is needed in reports,
  map it from `attributes`.
- **Collapse of identical closely-spaced DNS queries** with no source id (same ts+client+domain+action →
  same hash → dedup merges them): for "hits per site" counts could undercount. Consider a counter
  or source id when available.
- **DNS cursor not re-verified in new tests** (review Task 2): cursor advancement for `source='dns'`
  is covered only indirectly (cursor logic is generic and already proven in 3A). Add an explicit
  assertion if higher coverage is desired.
