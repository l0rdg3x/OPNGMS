# Syslog Pipeline — Phase 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A tenant-scoped, backend-mediated log search API + an in-app "Logs" investigation page over the Phase-1 OpenSearch log lake.

**Architecture:** A backend `log_search` service builds an OpenSearch `_search` body that ALWAYS injects a `filter` clause for the path tenant (+ time range, optional device), accepts a guarded Lucene `query_string`, and queries `opngms-logs-*` over the internal HTTP. A `POST .../logs/search` endpoint (new `LOG_VIEW` RBAC, tenant_admin+operator) returns hits with inline `_source`. A React "Logs" page drives it.

**Tech Stack:** Python 3.14 · FastAPI · httpx · OpenSearch `_search` · React 19 + Mantine v9 (+ @mantine/dates) + openapi-fetch · pytest + respx · vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-06-12-syslog-pipeline-phase2-design.md`
**Branch:** `feat/syslog-pipeline-phase2` (already created).

---

## File Structure

**Backend — create:** `app/services/log_search.py`, `app/schemas/logs.py`, `app/api/logs.py`, tests.
**Backend — modify:** `app/core/rbac.py` (LOG_VIEW), `app/core/config.py` (caps), `app/main.py` (router).
**Frontend — create:** `frontend/src/logs/logHooks.ts`, `frontend/src/pages/LogsPage.tsx`, tests.
**Frontend — modify:** `frontend/src/api/schema.d.ts` (regen), `frontend/src/components/AppShell.tsx` (route + nav).

---

## Conventions
- Backend DB tests prefix: `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`. Pure tests (no `db_engine`) always run.
- Frontend: before any PR run `npm run build` (tsc -b + vite). English everywhere; commit after each task.

---

# PHASE A — backend

## Task A1: RBAC LOG_VIEW + settings + `build_search_body` (pure)

**Files:**
- Modify: `app/core/rbac.py`, `app/core/config.py`
- Create: `app/services/log_search.py` (this task: the pure body builder + module constants)
- Test: `tests/test_log_search_body.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_log_search_body.py
import uuid
from datetime import UTC, datetime

from app.services.log_search import MAX_SIZE, build_search_body


def _rng():
    return datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)


def test_tenant_and_range_filters_always_present():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="", device_id=None, page=0, size=50)
    flt = body["query"]["bool"]["filter"]
    assert {"term": {"tenant_id": str(tid)}} in flt
    assert any("range" in c and "@timestamp" in c["range"] for c in flt)
    assert body["sort"] == [{"@timestamp": "desc"}]
    assert body["from"] == 0 and body["size"] == 50
    assert body["track_total_hits"] is True
    assert "must" not in body["query"]["bool"]  # no query -> no must clause


def test_device_filter_added():
    tid, did = uuid.uuid4(), uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="", device_id=did, page=0, size=10)
    assert {"term": {"device_id": str(did)}} in body["query"]["bool"]["filter"]


def test_query_string_is_guarded_and_in_must():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="action:block", device_id=None, page=2, size=25)
    must = body["query"]["bool"]["must"]
    qs = must[0]["query_string"]
    assert qs["query"] == "action:block"
    assert qs["allow_leading_wildcard"] is False
    assert qs["default_field"] == "message"
    assert body["from"] == 2 * 25


def test_malicious_tenant_in_query_cannot_widen():
    # a query that tries to escape the tenant scope stays in must; the filter is unchanged
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="tenant_id:other", device_id=None, page=0, size=10)
    assert {"term": {"tenant_id": str(tid)}} in body["query"]["bool"]["filter"]
    assert body["query"]["bool"]["must"][0]["query_string"]["query"] == "tenant_id:other"


def test_size_clamped_to_max():
    tid = uuid.uuid4()
    frm, to = _rng()
    body = build_search_body(tenant_id=tid, frm=frm, to=to, query="", device_id=None, page=0, size=9999)
    assert body["size"] == MAX_SIZE
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_log_search_body.py -v`
Expected: FAIL (ModuleNotFoundError).

- [ ] **Step 3: RBAC + settings**

`app/core/rbac.py`: add to `Action` (after `REPORT_CONFIG`):
```python
    LOG_VIEW = "log.view"
```
and to `_TENANT_MATRIX`:
```python
    Action.LOG_VIEW: {TENANT_ADMIN, OPERATOR},
```

`app/core/config.py`: add to `Settings`:
```python
    log_search_max_size: int = 200
    log_search_max_range_days: int = 31
```

- [ ] **Step 4: Implement the body builder** (`app/services/log_search.py`)

```python
"""Tenant-scoped OpenSearch log search: query builder + HTTP client (the only OpenSearch client)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

MAX_SIZE = 200


def build_search_body(*, tenant_id: uuid.UUID, frm: datetime, to: datetime, query: str,
                      device_id: uuid.UUID | None, page: int, size: int) -> dict:
    """Build the OpenSearch _search body. The tenant_id + time-range filters are ALWAYS present;
    a non-empty `query` becomes a guarded query_string in `must` (ANDed with the filter — it can
    never widen past the tenant scope)."""
    filters: list[dict] = [
        {"term": {"tenant_id": str(tenant_id)}},
        {"range": {"@timestamp": {"gte": frm.isoformat(), "lte": to.isoformat()}}},
    ]
    if device_id is not None:
        filters.append({"term": {"device_id": str(device_id)}})
    bool_q: dict = {"filter": filters}
    if query:
        bool_q["must"] = [{
            "query_string": {
                "query": query,
                "default_field": "message",
                "allow_leading_wildcard": False,
                "analyze_wildcard": False,
                "lenient": True,
            }
        }]
    return {
        "query": {"bool": bool_q},
        "sort": [{"@timestamp": "desc"}],
        "from": max(0, page) * min(size, MAX_SIZE),
        "size": min(size, MAX_SIZE),
        "track_total_hits": True,
    }


@dataclass
class LogHit:
    id: str
    timestamp: str
    device_id: str
    host: str
    program: str
    message: str
    source: dict


@dataclass
class SearchResult:
    total: int
    hits: list[LogHit]


class LogSearchError(Exception):
    """OpenSearch transport/query failure (mapped to 502 by the API)."""
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_log_search_body.py -v`
Expected: PASS. `.venv/bin/ruff check app/services/log_search.py app/core/rbac.py` clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/core/rbac.py backend/app/core/config.py backend/app/services/log_search.py backend/tests/test_log_search_body.py
git commit -m "feat(logs): LOG_VIEW RBAC + tenant-scoped OpenSearch query builder"
```

---

## Task A2: `search_logs` HTTP client

**Files:**
- Modify: `app/services/log_search.py`
- Test: `tests/test_log_search_client.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_log_search_client.py
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.services.log_search import LogSearchError, search_logs


class _S:
    opensearch_url = "http://opensearch:9200"


@respx.mock
async def test_search_maps_hits():
    respx.post("http://opensearch:9200/opngms-logs-*/_search").mock(return_value=httpx.Response(200, json={
        "hits": {"total": {"value": 2}, "hits": [
            {"_id": "a", "_source": {"@timestamp": "2026-06-01T00:00:00Z", "tenant_id": "t", "device_id": "d",
                                     "host": "fw", "program": "filterlog", "message": "blocked"}},
        ]},
    }))
    res = await search_logs(_S(), tenant_id=uuid.uuid4(), frm=datetime(2026, 6, 1, tzinfo=UTC),
                            to=datetime(2026, 6, 2, tzinfo=UTC), query="", device_id=None, page=0, size=10)
    assert res.total == 2
    assert res.hits[0].id == "a"
    assert res.hits[0].program == "filterlog"
    assert res.hits[0].message == "blocked"
    assert res.hits[0].source["host"] == "fw"


@respx.mock
async def test_search_error_maps_to_logsearcherror():
    respx.post("http://opensearch:9200/opngms-logs-*/_search").mock(return_value=httpx.Response(500, json={"error": "x"}))
    with pytest.raises(LogSearchError):
        await search_logs(_S(), tenant_id=uuid.uuid4(), frm=datetime(2026, 6, 1, tzinfo=UTC),
                          to=datetime(2026, 6, 2, tzinfo=UTC), query="", device_id=None, page=0, size=10)
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Append `search_logs` to `app/services/log_search.py`**

```python
import httpx  # noqa: E402  (add to the top imports if preferred)


async def search_logs(settings, *, tenant_id, frm, to, query, device_id, page, size) -> SearchResult:
    """POST the search to OpenSearch (internal URL, plain HTTP) and map the response."""
    body = build_search_body(tenant_id=tenant_id, frm=frm, to=to, query=query,
                             device_id=device_id, page=page, size=size)
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LogSearchError(str(exc)[:200]) from exc
    total = (data.get("hits", {}).get("total", {}) or {}).get("value", 0)
    hits: list[LogHit] = []
    for h in data.get("hits", {}).get("hits", []):
        src = h.get("_source", {}) or {}
        hits.append(LogHit(
            id=str(h.get("_id", "")),
            timestamp=str(src.get("@timestamp", "")),
            device_id=str(src.get("device_id", "")),
            host=str(src.get("host", "")),
            program=str(src.get("program", "")),
            message=str(src.get("message", "")),
            source=src,
        ))
    return SearchResult(total=int(total), hits=hits)
```
(Prefer moving `import httpx` to the module top with the other imports; drop the `# noqa`.)

- [ ] **Step 4: Run to verify pass** (2 passed) + ruff clean.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/log_search.py backend/tests/test_log_search_client.py
git commit -m "feat(logs): OpenSearch search client + response mapping"
```

---

## Task A3: Search API + schemas

**Files:**
- Create: `app/schemas/logs.py`, `app/api/logs.py`
- Modify: `app/main.py`
- Test: `tests/test_logs_api.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_logs_api.py
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_membership, make_user


def _patch_search(monkeypatch, captured):
    import app.api.logs as mod
    from app.services.log_search import LogHit, SearchResult

    async def fake(settings, *, tenant_id, frm, to, query, device_id, page, size):
        captured["tenant_id"] = tenant_id
        captured["query"] = query
        return SearchResult(total=1, hits=[LogHit(id="x", timestamp="2026-06-01T00:00:00Z",
                            device_id="d", host="fw", program="filterlog", message="m", source={"a": 1})])
    monkeypatch.setattr(mod, "search_logs", fake)


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        op = await make_user(s, email="op@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=op.id, tenant_id=tid, role="operator")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"}); assert r.status_code == 200


async def test_operator_can_search_tenant_scoped(api_client, db_engine, monkeypatch):
    captured = {}
    _patch_search(monkeypatch, captured)
    tid, did = await _seed(db_engine)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/logs/search", json={
        "query": "action:block", "frm": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1 and body["hits"][0]["source"] == {"a": 1}
    assert captured["tenant_id"] == tid          # tenant taken from the PATH, not the body
    assert captured["query"] == "action:block"


async def test_read_only_denied(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/logs/search", json={
        "frm": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z"})
    assert r.status_code == 403


async def test_bad_range_400(api_client, db_engine, monkeypatch):
    _patch_search(monkeypatch, {})
    tid, _ = await _seed(db_engine)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/logs/search", json={
        "frm": "2026-06-02T00:00:00Z", "to": "2026-06-01T00:00:00Z"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Schemas** (`app/schemas/logs.py`)

```python
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class LogSearchIn(BaseModel):
    query: str = Field(default="", max_length=2048)
    device_id: uuid.UUID | None = None
    frm: datetime
    to: datetime
    page: int = Field(default=0, ge=0)
    size: int = Field(default=100, ge=1)


class LogHitOut(BaseModel):
    id: str
    timestamp: str
    device_id: str
    host: str
    program: str
    message: str
    source: dict


class LogSearchOut(BaseModel):
    total: int
    hits: list[LogHitOut]
```

- [ ] **Step 4: API** (`app/api/logs.py`)

```python
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.schemas.logs import LogHitOut, LogSearchIn, LogSearchOut
from app.services.log_search import LogSearchError, search_logs

router = APIRouter(prefix="/api/tenants/{tenant_id}/logs", tags=["logs"])


@router.post("/search", response_model=LogSearchOut)
async def search_logs_endpoint(
    tenant_id: uuid.UUID,
    body: LogSearchIn,
    ctx: TenantContext = Depends(require_tenant(Action.LOG_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogSearchOut:
    s = get_settings()
    if body.to <= body.frm:
        raise HTTPException(status_code=400, detail="`to` must be after `frm`")
    if body.to - body.frm > timedelta(days=s.log_search_max_range_days):
        raise HTTPException(status_code=400, detail=f"range must not exceed {s.log_search_max_range_days} days")
    if body.device_id is not None:
        device = await session.get(Device, body.device_id)
        if device is None or device.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Device not found")
    try:
        res = await search_logs(s, tenant_id=tenant_id, frm=body.frm, to=body.to, query=body.query,
                                device_id=body.device_id, page=body.page, size=body.size)
    except LogSearchError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="log search unavailable") from exc
    return LogSearchOut(total=res.total, hits=[
        LogHitOut(id=h.id, timestamp=h.timestamp, device_id=h.device_id, host=h.host,
                  program=h.program, message=h.message, source=h.source) for h in res.hits])
```
Mount in `app/main.py`: `from app.api.logs import router as logs_router` + `app.include_router(logs_router)`.

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_logs_api.py -v`
Expected: 3 passed. `.venv/bin/ruff check app/api/logs.py app/schemas/logs.py` clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas/logs.py backend/app/api/logs.py backend/app/main.py backend/tests/test_logs_api.py
git commit -m "feat(logs): tenant-scoped log search API (LOG_VIEW)"
```

---

# PHASE B — frontend

## Task B1: Logs page

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (regen), `frontend/src/components/AppShell.tsx`
- Create: `frontend/src/logs/logHooks.ts`, `frontend/src/pages/LogsPage.tsx`, `frontend/src/pages/__tests__/logs.test.tsx`

- [ ] **Step 1: Regenerate the client**

Run: `cd frontend && npm run gen:api` then `grep -c "logs/search" src/api/schema.d.ts` → > 0.

- [ ] **Step 2: Write the failing test** (mirror the local `withTenant` + MSW pattern from `src/pages/__tests__/reportSchedule.test.tsx`)

```tsx
// frontend/src/pages/__tests__/logs.test.tsx
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogsPage } from "../LogsPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "operator") {
  return (
    <TenantContext.Provider value={{
      tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
      activeId: "t1", setActiveId: () => {}, loading: false,
    }}>{node}</TenantContext.Provider>
  );
}

const SEARCH = "http://localhost:3000/api/tenants/t1/logs/search";
const DEVICES = "http://localhost:3000/api/tenants/t1/devices";

describe("LogsPage", () => {
  it("runs a search and shows results + raw doc modal", async () => {
    let body: unknown = null;
    server.use(
      http.get(DEVICES, () => HttpResponse.json([{ id: "d1", name: "fw-1" }])),
      http.post(SEARCH, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ total: 1, hits: [{ id: "h1", timestamp: "2026-06-01T00:00:00Z",
          device_id: "d1", host: "fw", program: "filterlog", message: "blocked 1.2.3.4", source: { a: 1 } }] });
      }),
    );
    renderWithProviders(withTenant(<LogsPage />, "operator"));
    await userEvent.type(await screen.findByTestId("logs-query"), "action:block");
    await userEvent.click(screen.getByTestId("logs-search"));
    await waitFor(() => expect((body as { query: string }).query).toBe("action:block"));
    expect(await screen.findByText(/blocked 1.2.3.4/)).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("logrow-h1"));
    expect(await screen.findByTestId("logs-raw")).toBeInTheDocument();
  });

  it("blocks read_only", () => {
    server.use(http.get(DEVICES, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<LogsPage />, "read_only"));
    expect(screen.getByTestId("logs-forbidden")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify it fails** (`cd frontend && npm test -- logs`).

- [ ] **Step 4: Hooks** (`frontend/src/logs/logHooks.ts`)

```ts
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type LogSearchOut = components["schemas"]["LogSearchOut"];
export type LogSearchIn = components["schemas"]["LogSearchIn"];

export function useLogSearch() {
  const { activeId } = useTenant();
  return useMutation({
    mutationFn: async (body: LogSearchIn): Promise<LogSearchOut> => {
      const { data, error } = await api.POST("/api/tenants/{tenant_id}/logs/search",
        { params: { path: { tenant_id: activeId! } }, body });
      if (error || !data) throw new Error("Log search failed");
      return data;
    },
  });
}
```

- [ ] **Step 5: Page** (`frontend/src/pages/LogsPage.tsx`)

```tsx
import { useState } from "react";
import {
  Alert, Button, Card, Code, Group, Modal, Select, Stack, Table, Text, TextInput, Title,
} from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";

import { useTenant } from "../tenant/useTenant";
import { useTenantDevices } from "../templates/settingHooks";
import { useLogSearch, type LogSearchOut } from "../logs/logHooks";

function isoDaysAgo(days: number): Date {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d;
}

export function LogsPage() {
  const { activeId, tenants } = useTenant();
  const role = tenants.find((tn) => tn.id === activeId)?.role ?? null;
  const devices = useTenantDevices();
  const search = useLogSearch();
  const [query, setQuery] = useState("");
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [frm, setFrm] = useState<Date | null>(isoDaysAgo(1));
  const [to, setTo] = useState<Date | null>(new Date());
  const [result, setResult] = useState<LogSearchOut | null>(null);
  const [raw, setRaw] = useState<Record<string, unknown> | null>(null);

  if (role !== "tenant_admin" && role !== "operator") {
    return <Alert color="red" data-testid="logs-forbidden">Operators and tenant admins only.</Alert>;
  }

  const deviceName = (id: string) =>
    (devices.data ?? []).find((d) => d.id === id)?.name ?? id;

  async function run() {
    if (!frm || !to) return;
    const res = await search.mutateAsync({
      query, device_id: deviceId, frm: frm.toISOString(), to: to.toISOString(), page: 0, size: 100,
    } as never);
    setResult(res);
  }

  return (
    <Stack>
      <Title order={3}>Logs</Title>
      <Card withBorder padding="md" radius="md">
        <Stack>
          <Group grow>
            <DateTimePicker label="From" value={frm} onChange={(v) => setFrm(v as Date | null)} data-testid="logs-from" />
            <DateTimePicker label="To" value={to} onChange={(v) => setTo(v as Date | null)} data-testid="logs-to" />
            <Select label="Device" clearable data={(devices.data ?? []).map((d) => ({ value: d.id, label: d.name }))}
                    value={deviceId} onChange={setDeviceId} data-testid="logs-device" />
          </Group>
          <TextInput label="Query (Lucene)" placeholder="e.g. action:block AND src_ip:10.0.0.1"
                     value={query} onChange={(e) => setQuery(e.currentTarget.value)} data-testid="logs-query" />
          <Group>
            <Button onClick={run} loading={search.isPending} data-testid="logs-search">Search</Button>
            {result && <Text size="sm" c="dimmed">{result.total} matches</Text>}
          </Group>
        </Stack>
      </Card>

      {search.isError && <Alert color="red">Log search failed.</Alert>}

      {result && (
        <Table highlightOnHover>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Time</Table.Th><Table.Th>Device</Table.Th>
              <Table.Th>Program</Table.Th><Table.Th>Message</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {result.hits.map((h) => (
              <Table.Tr key={h.id} style={{ cursor: "pointer" }} onClick={() => setRaw(h.source)} data-testid={`logrow-${h.id}`}>
                <Table.Td>{h.timestamp}</Table.Td>
                <Table.Td>{deviceName(h.device_id)}</Table.Td>
                <Table.Td>{h.program}</Table.Td>
                <Table.Td>{h.message}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}

      <Modal opened={raw !== null} onClose={() => setRaw(null)} title="Raw document" size="lg">
        <Code block data-testid="logs-raw">{JSON.stringify(raw, null, 2)}</Code>
      </Modal>
    </Stack>
  );
}
```

- [ ] **Step 6: Route + nav** in `frontend/src/components/AppShell.tsx`: lazy-import `LogsPage` (named-export `.then` form like the SMTP page), a `<Route path="/logs" element={<LogsPage />} />`, and a nav item visible to tenant_admin/operator (mirror how the report-schedule nav gates by role; if nav uses i18n, add the label).

- [ ] **Step 7: Verify + build gate**

Run: `cd frontend && npm test -- logs && npm run build`
Both MUST pass. If `DateTimePicker`'s `onChange` value type differs in this Mantine version (it may pass a string), adjust the `setFrm`/`setTo` handlers + the `frm.toISOString()` accordingly so `tsc -b` passes.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/logs/logHooks.ts frontend/src/pages/LogsPage.tsx frontend/src/pages/__tests__/logs.test.tsx frontend/src/components/AppShell.tsx
git add frontend/src/i18n/en.ts 2>/dev/null || true
git commit -m "feat(logs): tenant log investigation page (search + raw doc)"
```

---

## Final verification

- [ ] **Backend:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Frontend:** `cd frontend && npm run build && npm test` → all pass.
- [ ] **Security review:** dispatch `security-reviewer` (the tenant filter is injected from the path tenant not the body; query_string can't escape the filter; LOG_VIEW excludes read_only; OpenSearch internal-only; no secret leakage). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge.

---

## Self-review notes (author)

- **Spec coverage:** LOG_VIEW RBAC (A1) ✓; settings caps (A1/A3) ✓; `build_search_body` mandatory tenant+range filter, guarded query_string, device filter, size clamp (A1) ✓; `search_logs` OpenSearch client + mapping + LogSearchError (A2) ✓; search API with range validation + device-in-tenant + 502 + tenant-from-path (A3) ✓; inline `source` (A3 schema) ✓; frontend Logs page (search bar, device filter, table, raw modal, LOG_VIEW gating) (B1) ✓; tenant-isolation security (filter injected from path) ✓.
- **Type consistency:** `build_search_body(*, tenant_id, frm, to, query, device_id, page, size)` and `search_logs(settings, *, …)` identical A1/A2/A3; `LogHit`/`SearchResult`/`LogSearchError` A1↔A2↔A3; `LogSearchOut`/`LogHitOut` schema ↔ API ↔ frontend.
- **Risk flag:** Mantine `DateTimePicker` onChange value type (Date vs string) is version-dependent — B1 Step 7 notes adjusting the handlers so the build passes.
