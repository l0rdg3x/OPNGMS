# Syslog Phase 3.3 — Scale (deep paging + multi-node config) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unbounded, stable deep paging for the tenant log search (PIT + `search_after`, "Load more" UI), plus a shipped multi-node OpenSearch config.

**Architecture:** Evolve the Phase-2 search to open an OpenSearch Point-In-Time and page with `search_after` over a `[@timestamp desc, _shard_doc asc]` sort (the tiebreaker our second-granularity timestamps need). The backend stays stateless — the `{pit_id, after}` cursor round-trips through the API and lives in the browser. A separate multi-node compose + replica index template + ops docs ship as config (HA verified later at a staging bring-up).

**Tech Stack:** Python 3.14 · FastAPI · httpx · OpenSearch PIT/`search_after` · React 19 + Mantine v9 + openapi-fetch · pytest + respx · vitest + MSW · docker-compose · PyYAML (test).

**Spec:** `docs/superpowers/specs/2026-06-12-syslog-phase3c-scale-design.md`
**Branch:** `feat/log-search-deep-paging` (already created off main).

---

## File Structure

**Backend — modify:** `app/services/log_search.py` (PIT + cursor), `app/schemas/logs.py` (cursor fields), `app/api/logs.py` (drop page/guard, pass cursor). **Rewrite tests:** `tests/test_log_search_body.py`, `tests/test_log_search_client.py`, `tests/test_logs_api.py`. **Create test:** `tests/test_multinode_config.py`.
**Frontend — modify:** `frontend/src/api/schema.d.ts` + `openapi.json` (regen), `frontend/src/logs/logHooks.ts`, `frontend/src/pages/LogsPage.tsx`, `frontend/src/pages/__tests__/logs.test.tsx`.
**Config — create:** `docker-compose.logs.multinode.yml`, `deploy/opensearch/index-template.multinode.json`; **modify** `README.md` (ops section).

---

## Conventions
- Backend DB tests prefix `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`; pure/respx tests always run. Commit from REPO ROOT with `backend/...`/`frontend/...` paths. English everywhere; commit per task. Frontend PR gate: `npm run build`.

---

# PHASE A — backend

## Task 1: `build_search_body` → PIT + `search_after`

**Files:**
- Modify: `backend/app/services/log_search.py`
- Test: `backend/tests/test_log_search_body.py` (rewrite)

- [ ] **Step 1: Rewrite the test** — replace `backend/tests/test_log_search_body.py` entirely

```python
import uuid
from datetime import UTC, datetime

from app.services.log_search import MAX_SIZE, build_search_body


def _rng():
    return datetime(2026, 6, 1, tzinfo=UTC), datetime(2026, 6, 2, tzinfo=UTC)


def _body(**kw):
    base = dict(tenant_id=uuid.uuid4(), frm=_rng()[0], to=_rng()[1], query="", device_id=None,
                size=50, pit_id="PITID")
    base.update(kw)
    return build_search_body(**base)


def test_pit_and_tiebreaker_sort_always_present():
    tid = uuid.uuid4()
    body = _body(tenant_id=tid)
    assert body["pit"]["id"] == "PITID" and "keep_alive" in body["pit"]
    assert body["sort"] == [{"@timestamp": "desc"}, {"_shard_doc": "asc"}]
    flt = body["query"]["bool"]["filter"]
    assert {"term": {"tenant_id": str(tid)}} in flt
    assert any("range" in c and "@timestamp" in c["range"] for c in flt)
    assert body["track_total_hits"] is True
    assert "from" not in body
    assert "search_after" not in body            # first page -> no cursor
    assert "must" not in body["query"]["bool"]


def test_search_after_added_when_continuing():
    body = _body(search_after=["2026-06-01T00:00:00Z", 42])
    assert body["search_after"] == ["2026-06-01T00:00:00Z", 42]


def test_device_filter_and_guarded_query():
    did = uuid.uuid4()
    body = _body(query="action:block", device_id=did)
    assert {"term": {"device_id": str(did)}} in body["query"]["bool"]["filter"]
    qs = body["query"]["bool"]["must"][0]["query_string"]
    assert qs["query"] == "action:block" and qs["allow_leading_wildcard"] is False


def test_malicious_tenant_in_query_cannot_widen():
    tid = uuid.uuid4()
    body = _body(tenant_id=tid, query="tenant_id:other")
    assert {"term": {"tenant_id": str(tid)}} in body["query"]["bool"]["filter"]
    assert body["query"]["bool"]["must"][0]["query_string"]["query"] == "tenant_id:other"


def test_size_clamped_to_max():
    body = _body(size=9999)
    assert body["size"] == MAX_SIZE
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_log_search_body.py -v` → FAIL (signature/shape mismatch).

- [ ] **Step 3: Rewrite `build_search_body`** in `backend/app/services/log_search.py`

Replace the `MAX_RESULT_WINDOW` constant with a PIT keep-alive constant and rewrite the builder (keep `MAX_SIZE`):

```python
MAX_SIZE = 200
PIT_KEEPALIVE = "2m"  # Point-In-Time TTL; refreshed on each search, expires the cursor when idle.


def build_search_body(*, tenant_id: uuid.UUID, frm: datetime, to: datetime, query: str,
                      device_id: uuid.UUID | None, size: int, pit_id: str,
                      search_after: list | None = None) -> dict:
    """Build the OpenSearch PIT `_search` body. The tenant_id + time-range filters are ALWAYS present
    (the tenant filter is injected from the RBAC-verified path — a query_string lands in `must` and can
    never widen past it). Paging is via PIT + search_after over a stable [@timestamp, _shard_doc] sort,
    so it is unbounded and consistent across the second-granularity timestamp ties our logs produce."""
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
    body: dict = {
        "query": {"bool": bool_q},
        "pit": {"id": pit_id, "keep_alive": PIT_KEEPALIVE},
        "sort": [{"@timestamp": "desc"}, {"_shard_doc": "asc"}],
        "size": min(size, MAX_SIZE),
        "track_total_hits": True,
    }
    if search_after is not None:
        body["search_after"] = search_after
    return body
```

Also add `next_cursor` to `SearchResult`:
```python
@dataclass
class SearchResult:
    total: int
    hits: list[LogHit]
    next_cursor: dict | None = None
```

- [ ] **Step 4: Run to verify pass + lint**

Run: `cd backend && .venv/bin/pytest tests/test_log_search_body.py -v` → 5 passed.
Run: `cd backend && .venv/bin/ruff check app/services/log_search.py` → clean.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/log_search.py backend/tests/test_log_search_body.py
git commit -m "feat(logs): PIT + search_after query body (unbounded deep paging)"
```

---

## Task 2: `open_pit` + `search_logs` cursor orchestration

**Files:**
- Modify: `backend/app/services/log_search.py`
- Test: `backend/tests/test_log_search_client.py` (rewrite)

- [ ] **Step 1: Rewrite the test** — replace `backend/tests/test_log_search_client.py` entirely

```python
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from app.services.log_search import LogSearchError, search_logs


class _S:
    opensearch_url = "http://opensearch:9200"


_PIT = "http://opensearch:9200/opngms-logs-*/_pit"
_SEARCH = "http://opensearch:9200/_search"


def _args(**kw):
    base = dict(tenant_id=uuid.uuid4(), frm=datetime(2026, 6, 1, tzinfo=UTC),
                to=datetime(2026, 6, 2, tzinfo=UTC), query="", device_id=None, size=2)
    base.update(kw)
    return base


def _hit(i):
    return {"_id": str(i), "_source": {"@timestamp": f"2026-06-01T00:00:0{i}Z", "device_id": "d",
            "host": "fw", "program": "filterlog", "message": f"m{i}"}, "sort": [i, i]}


@respx.mock
async def test_first_page_opens_pit_and_returns_cursor():
    respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "PIT1"}))
    respx.post(_SEARCH).mock(return_value=httpx.Response(200, json={
        "pit_id": "PIT1", "hits": {"total": {"value": 9}, "hits": [_hit(1), _hit(2)]}}))
    res = await search_logs(_S(), **_args())
    assert res.total == 9 and len(res.hits) == 2
    assert res.hits[0].message == "m1"
    # full page (size 2 -> 2 hits) => a next cursor carrying the last hit's sort
    assert res.next_cursor == {"pit_id": "PIT1", "after": [2, 2]}


@respx.mock
async def test_partial_page_has_no_next_cursor():
    respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "PIT1"}))
    respx.post(_SEARCH).mock(return_value=httpx.Response(200, json={
        "pit_id": "PIT1", "hits": {"total": {"value": 1}, "hits": [_hit(1)]}}))
    res = await search_logs(_S(), **_args(size=2))
    assert res.next_cursor is None


@respx.mock
async def test_continuation_reuses_cursor_and_skips_pit():
    pit_route = respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "X"}))
    captured = {}

    def _search_responder(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"pit_id": "PIT1", "hits": {"total": {"value": 9}, "hits": [_hit(3)]}})

    respx.post(_SEARCH).mock(side_effect=_search_responder)
    res = await search_logs(_S(), **_args(size=2, cursor={"pit_id": "PIT1", "after": [2, 2]}))
    assert not pit_route.called                     # continuation must NOT open a new PIT
    assert captured["pit"]["id"] == "PIT1"
    assert captured["search_after"] == [2, 2]
    assert res.next_cursor is None                  # 1 hit < size 2


@respx.mock
async def test_search_error_maps_to_logsearcherror():
    respx.post(_PIT).mock(return_value=httpx.Response(200, json={"pit_id": "PIT1"}))
    respx.post(_SEARCH).mock(return_value=httpx.Response(500, json={"error": "x"}))
    with pytest.raises(LogSearchError):
        await search_logs(_S(), **_args())
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `open_pit` + rewrite `search_logs`** in `backend/app/services/log_search.py`

Replace the existing `search_logs` with:

```python
async def open_pit(settings) -> str:
    """Open an OpenSearch Point-In-Time over the log indices; returns its id."""
    url = f"{settings.opensearch_url}/opngms-logs-*/_pit"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"keep_alive": PIT_KEEPALIVE, "ignore_unavailable": "true"})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LogSearchError(str(exc)[:200]) from exc
    pit_id = data.get("pit_id") or data.get("id")
    if not pit_id:
        raise LogSearchError("OpenSearch did not return a pit_id")
    return str(pit_id)


async def search_logs(settings, *, tenant_id, frm, to, query, device_id, size, cursor=None) -> SearchResult:
    """Tenant-scoped PIT + search_after search. With no cursor, opens a PIT and returns page 1; with a
    cursor ({pit_id, after}) it continues. Returns hits + a next_cursor (None on the last page)."""
    eff_size = min(size, MAX_SIZE)
    if cursor is None:
        pit_id = await open_pit(settings)
        search_after = None
    else:
        pit_id = cursor["pit_id"]
        search_after = cursor["after"]
    body = build_search_body(tenant_id=tenant_id, frm=frm, to=to, query=query, device_id=device_id,
                             size=eff_size, pit_id=pit_id, search_after=search_after)
    url = f"{settings.opensearch_url}/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LogSearchError(str(exc)[:200]) from exc
    total = (data.get("hits", {}).get("total", {}) or {}).get("value", 0)
    hits: list[LogHit] = []
    last_sort = None
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
        last_sort = h.get("sort")
    next_cursor = None
    if len(hits) == eff_size and last_sort is not None:
        next_cursor = {"pit_id": str(data.get("pit_id") or pit_id), "after": last_sort}
    return SearchResult(total=int(total), hits=hits, next_cursor=next_cursor)
```

- [ ] **Step 4: Run to verify pass + lint** (4 passed; ruff clean on `app/services/log_search.py`).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/log_search.py backend/tests/test_log_search_client.py
git commit -m "feat(logs): PIT open + cursor-based search_logs (next_cursor)"
```

---

## Task 3: API + schemas — cursor in, next_cursor out

**Files:**
- Modify: `backend/app/schemas/logs.py`, `backend/app/api/logs.py`
- Test: `backend/tests/test_logs_api.py` (update)

- [ ] **Step 1: Update the test** — edit `backend/tests/test_logs_api.py`

The `_patch_search` fake's signature changes (no `page`; add `size`/`cursor`); the deep-paging test is removed; a cursor round-trip test is added. Replace the `_patch_search` helper and the `test_deep_paging_rejected_400` test:

Replace the fake inside `_patch_search`:
```python
    async def fake(settings, *, tenant_id, frm, to, query, device_id, size, cursor=None):
        captured["tenant_id"] = tenant_id
        captured["query"] = query
        captured["size"] = size
        captured["cursor"] = cursor
        return SearchResult(total=1, hits=[LogHit(id="x", timestamp="2026-06-01T00:00:00Z",
                            device_id="d", host="fw", program="filterlog", message="m", source={"a": 1})],
                            next_cursor={"pit_id": "P", "after": [1, 1]})
```
Delete `test_deep_paging_rejected_400` entirely. Update `test_size_clamped_to_setting` to drop `page` (it sends `size:200` already; no `page` field needed). Add this test:
```python
async def test_cursor_round_trips_and_returns_next(api_client, db_engine, monkeypatch):
    captured = {}
    _patch_search(monkeypatch, captured)
    tid, _ = await _seed(db_engine)
    await _login(api_client, "op@x.io")
    r = await api_client.post(f"/api/tenants/{tid}/logs/search", json={
        "frm": "2026-06-01T00:00:00Z", "to": "2026-06-02T00:00:00Z",
        "cursor": {"pit_id": "PIT1", "after": ["2026-06-01T00:00:00Z", 7]}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_cursor"] == {"pit_id": "P", "after": [1, 1]}
    assert captured["cursor"] == {"pit_id": "PIT1", "after": ["2026-06-01T00:00:00Z", 7]}
```
Keep `test_operator_can_search_tenant_scoped`, `test_read_only_denied`, `test_bad_range_400`, `test_cross_tenant_device_404`, `test_naive_datetime_rejected_422` (these send no `page`, so they still pass).

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Schemas** — `backend/app/schemas/logs.py`

```python
import uuid
from typing import Any

from pydantic import AwareDatetime, BaseModel, Field


class LogCursor(BaseModel):
    pit_id: str = Field(max_length=8192)
    after: list[Any]


class LogSearchIn(BaseModel):
    query: str = Field(default="", max_length=2048)
    device_id: uuid.UUID | None = None
    frm: AwareDatetime
    to: AwareDatetime
    size: int = Field(default=100, ge=1)
    cursor: LogCursor | None = None


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
    next_cursor: LogCursor | None = None
```

- [ ] **Step 4: API** — `backend/app/api/logs.py`

Change the import (drop `MAX_RESULT_WINDOW`):
```python
from app.services.log_search import MAX_SIZE, LogSearchError, search_logs
```
Rewrite the handler body — remove the deep-paging guard + `page`, pass `cursor`, return `next_cursor`:
```python
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
        raise HTTPException(
            status_code=400,
            detail=f"range must not exceed {s.log_search_max_range_days} days",
        )
    effective_size = min(body.size, s.log_search_max_size, MAX_SIZE)
    if body.device_id is not None:
        device = await session.get(Device, body.device_id)
        if device is None or device.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Device not found")
    try:
        res = await search_logs(
            s, tenant_id=tenant_id, frm=body.frm, to=body.to, query=body.query,
            device_id=body.device_id, size=effective_size,
            cursor=body.cursor.model_dump() if body.cursor else None,
        )
    except LogSearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="log search unavailable"
        ) from exc
    return LogSearchOut(
        total=res.total,
        next_cursor=res.next_cursor,
        hits=[
            LogHitOut(id=h.id, timestamp=h.timestamp, device_id=h.device_id, host=h.host,
                      program=h.program, message=h.message, source=h.source)
            for h in res.hits
        ],
    )
```

- [ ] **Step 5: Run to verify pass + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_logs_api.py -v` → all pass.
Run: `cd backend && .venv/bin/ruff check app/api/logs.py app/schemas/logs.py` → clean.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/logs.py backend/app/api/logs.py backend/tests/test_logs_api.py
git commit -m "feat(logs): cursor-paged search API (next_cursor, no result-window cap)"
```

---

# PHASE B — frontend

## Task 4: "Load more" pagination

**Files:**
- Modify: `frontend/src/api/schema.d.ts` + `openapi.json` (regen), `frontend/src/logs/logHooks.ts`, `frontend/src/pages/LogsPage.tsx`, `frontend/src/pages/__tests__/logs.test.tsx`

- [ ] **Step 1: Regenerate the client**

Run: `cd frontend && npm run gen:api` then `grep -c "next_cursor" src/api/schema.d.ts` → > 0.

- [ ] **Step 2: Update the test** — replace `frontend/src/pages/__tests__/logs.test.tsx`

```tsx
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

function hit(id: string, msg: string) {
  return { id, timestamp: "2026-06-01T00:00:00Z", device_id: "d1", host: "fw",
           program: "filterlog", message: msg, source: { a: id } };
}

describe("LogsPage", () => {
  it("searches, loads more (appends), then exhausts the cursor", async () => {
    const bodies: Array<{ cursor?: unknown }> = [];
    let call = 0;
    server.use(
      http.get(DEVICES, () => HttpResponse.json([{ id: "d1", name: "fw-1" }])),
      http.post(SEARCH, async ({ request }) => {
        bodies.push((await request.json()) as { cursor?: unknown });
        call += 1;
        if (call === 1) {
          return HttpResponse.json({ total: 2, hits: [hit("h1", "first")],
            next_cursor: { pit_id: "P", after: [1, 1] } });
        }
        return HttpResponse.json({ total: 2, hits: [hit("h2", "second")], next_cursor: null });
      }),
    );
    renderWithProviders(withTenant(<LogsPage />, "operator"));
    await userEvent.click(await screen.findByTestId("logs-search"));
    expect(await screen.findByText(/first/)).toBeInTheDocument();
    await userEvent.click(await screen.findByTestId("logs-loadmore"));
    expect(await screen.findByText(/second/)).toBeInTheDocument();
    // both pages are shown (append, not replace)
    expect(screen.getByText(/first/)).toBeInTheDocument();
    // second request carried the cursor; the button is gone once exhausted
    await waitFor(() => expect((bodies[1] as { cursor?: unknown }).cursor).toEqual({ pit_id: "P", after: [1, 1] }));
    expect(screen.queryByTestId("logs-loadmore")).toBeNull();
  });

  it("blocks read_only", () => {
    server.use(http.get(DEVICES, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<LogsPage />, "read_only"));
    expect(screen.getByTestId("logs-forbidden")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run to verify it fails** (`cd frontend && npm test -- logs`).

- [ ] **Step 4: Hooks** — replace `frontend/src/logs/logHooks.ts`

```ts
import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type LogSearchOut = components["schemas"]["LogSearchOut"];
export type LogSearchIn = components["schemas"]["LogSearchIn"];
export type LogCursor = components["schemas"]["LogCursor"];

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

- [ ] **Step 5: Page** — update `frontend/src/pages/LogsPage.tsx`

Replace the state + `run` + the results render. Key changes: accumulate `hits`, hold `cursor`/`total`, add `loadMore`, render a "Load more" button when `cursor != null`.

Replace the state block:
```tsx
  const [result] = useState<LogSearchOut | null>(null); // (removed; see hits/cursor below)
```
with:
```tsx
  const [hits, setHits] = useState<LogSearchOut["hits"]>([]);
  const [cursor, setCursor] = useState<LogSearchOut["next_cursor"]>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [searched, setSearched] = useState(false);
```
Replace `run` with a page-fetcher + new-search + load-more:
```tsx
  async function fetchPage(c: LogSearchOut["next_cursor"]) {
    if (!frm || !to) return;
    try {
      const res = await search.mutateAsync({
        query, device_id: deviceId, frm: toIso(frm), to: toIso(to), size: 100,
        ...(c ? { cursor: c } : {}),
      } as LogSearchIn);
      setHits((prev) => (c ? [...prev, ...res.hits] : res.hits));
      setCursor(res.next_cursor ?? null);
      setTotal(res.total);
      setSearched(true);
    } catch {
      // search.isError drives the alert
    }
  }
  const run = () => { setHits([]); setCursor(null); setTotal(null); fetchPage(null); };
  const loadMore = () => fetchPage(cursor);
```
(Import `LogSearchIn` from `../logs/logHooks` alongside `useLogSearch`/`LogSearchOut`.)
In the search-button `Group`, replace the matches `Text`:
```tsx
            {total !== null && (
              <Text size="sm" c="dimmed" data-testid="logs-count">
                showing {hits.length} of {total}
              </Text>
            )}
```
Replace the `{result && (<Table>…</Table>)}` block to render from `hits` (guard on `searched`), iterate `hits`, and add the "Load more" button after the table:
```tsx
      {searched && (
        <Stack>
          <Table highlightOnHover>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>Time</Table.Th><Table.Th>Device</Table.Th>
                <Table.Th>Program</Table.Th><Table.Th>Message</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {hits.map((h) => (
                <Table.Tr key={h.id} style={{ cursor: "pointer" }} onClick={() => setRaw(h.source)} data-testid={`logrow-${h.id}`}>
                  <Table.Td>{h.timestamp}</Table.Td>
                  <Table.Td>{deviceName(h.device_id)}</Table.Td>
                  <Table.Td>{h.program}</Table.Td>
                  <Table.Td>{h.message}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
          {cursor && (
            <Button variant="default" onClick={loadMore} loading={search.isPending} data-testid="logs-loadmore">
              Load more
            </Button>
          )}
        </Stack>
      )}
```
Remove the now-unused `result` state and the old `{result && …}` count text. Ensure `setRaw`/`deviceName`/modal stay. `h.source` is `unknown`/`Record` — keep the existing `setRaw(h.source)` typing (cast as the existing code did if needed).

- [ ] **Step 6: Verify + build gate**

Run: `cd frontend && npm test -- logs && npm run build` → both pass. Fix any tsc issues (the `cursor` spread typing; `h.source` cast for `setRaw`).

- [ ] **Step 7: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/logs/logHooks.ts frontend/src/pages/LogsPage.tsx frontend/src/pages/__tests__/logs.test.tsx
git commit -m "feat(logs): Load more pagination over the search_after cursor"
```

---

# PHASE C — multi-node config

## Task 5: Multi-node OpenSearch config + ops docs

**Files:**
- Create: `docker-compose.logs.multinode.yml`, `deploy/opensearch/index-template.multinode.json`, `backend/tests/test_multinode_config.py`
- Modify: `README.md`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_multinode_config.py`

```python
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_multinode_compose_defines_three_nodes_no_single_node():
    data = yaml.safe_load((ROOT / "docker-compose.logs.multinode.yml").read_text())
    svcs = data["services"]
    for n in ("opensearch-n1", "opensearch-n2", "opensearch-n3"):
        assert n in svcs, f"missing {n}"
    assert "single-node" not in json.dumps(svcs)          # cluster discovery, not single-node
    assert "syslog-ng" in svcs and "syslog-bootstrap" in svcs


def test_multinode_index_template_is_replicated():
    tpl = json.loads((ROOT / "deploy/opensearch/index-template.multinode.json").read_text())
    settings = tpl["template"]["settings"]
    assert settings["number_of_replicas"] == 1
    assert settings["number_of_shards"] == 2
```

- [ ] **Step 2: Run to verify it fails** (`cd backend && .venv/bin/pytest tests/test_multinode_config.py -v` → FileNotFoundError).

- [ ] **Step 3: Create `docker-compose.logs.multinode.yml`**

(A complete multi-node ALTERNATIVE to `docker-compose.logs.yml` — used as `-f docker-compose.prod.yml -f docker-compose.logs.multinode.yml`, NOT together with the single-node file.)

```yaml
# Opt-in log lake — MULTI-NODE OpenSearch (3 nodes, HA). Use INSTEAD of docker-compose.logs.yml:
#   docker compose -f docker-compose.prod.yml -f docker-compose.logs.multinode.yml up -d
# Verify HA at a staging bring-up (node loss -> index stays green). Internal-only, no published port.

x-os-common: &os-common
  image: opensearchproject/opensearch:2.17.1
  environment: &os-env
    cluster.name: opngms-logs
    bootstrap.memory_lock: "true"
    DISABLE_SECURITY_PLUGIN: "true"
    OPENSEARCH_JAVA_OPTS: "-Xms512m -Xmx512m"
    TZ: ${TZ:-UTC}
    discovery.seed_hosts: opensearch-n1,opensearch-n2,opensearch-n3
    cluster.initial_cluster_manager_nodes: opensearch-n1,opensearch-n2,opensearch-n3
  ulimits:
    memlock: { soft: -1, hard: -1 }
  restart: unless-stopped

services:
  opensearch-n1:
    <<: *os-common
    environment:
      <<: *os-env
      node.name: opensearch-n1
    volumes:
      - opngms_os_n1:/usr/share/opensearch/data

  opensearch-n2:
    <<: *os-common
    environment:
      <<: *os-env
      node.name: opensearch-n2
    volumes:
      - opngms_os_n2:/usr/share/opensearch/data

  opensearch-n3:
    <<: *os-common
    environment:
      <<: *os-env
      node.name: opensearch-n3
    volumes:
      - opngms_os_n3:/usr/share/opensearch/data

  syslog-bootstrap:
    image: opngms-backend:latest
    command: ["python", "-m", "app.cli", "syslog-bootstrap", "--cert-dir", "/certs"]
    env_file: .env
    environment:
      TZ: ${TZ:-UTC}
      OPENSEARCH_URL: ${OPENSEARCH_URL:-http://opensearch-n1:9200}
    volumes:
      - opngms_syslog_certs:/certs
    depends_on:
      opensearch-n1:
        condition: service_started
      migrate:
        condition: service_completed_successfully
    restart: "no"

  syslog-ng:
    image: balabit/syslog-ng:4.5.0
    command: ["--no-caps", "-F"]
    environment:
      OPENSEARCH_URL: ${OPENSEARCH_URL:-http://opensearch-n1:9200}
      TZ: ${TZ:-UTC}
    ports:
      - "${SYSLOG_TLS_PORT:-6514}:6514"
    volumes:
      - ./deploy/syslog-ng/syslog-ng.conf:/etc/syslog-ng/syslog-ng.conf:ro
      - opngms_syslog_certs:/certs:ro
      - opngms_syslog_buffer:/var/lib/syslog-ng
    depends_on:
      syslog-bootstrap:
        condition: service_completed_successfully
    restart: unless-stopped

volumes:
  opngms_os_n1:
  opngms_os_n2:
  opngms_os_n3:
  opngms_syslog_certs:
  opngms_syslog_buffer:
```

- [ ] **Step 4: Create `deploy/opensearch/index-template.multinode.json`**

```json
{
  "index_patterns": ["opngms-logs-*"],
  "template": {
    "settings": {
      "number_of_shards": 2,
      "number_of_replicas": 1,
      "plugins.index_state_management.policy_id": "opngms-logs-retention"
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "tenant_id": { "type": "keyword" },
        "device_id": { "type": "keyword" },
        "host": { "type": "keyword" },
        "program": { "type": "keyword" },
        "message": { "type": "text" }
      }
    }
  }
}
```

- [ ] **Step 5: README ops section** — add to `README.md` under the Log lake section

Add a "### High availability (multi-node OpenSearch)" subsection documenting:
- Run with `docker compose -f docker-compose.prod.yml -f docker-compose.logs.multinode.yml up -d` **instead of** the single-node `docker-compose.logs.yml` (they are alternatives — do not combine).
- It runs a 3-node cluster; apply `deploy/opensearch/index-template.multinode.json` (2 shards, 1 replica) so each shard has a replica on another node → a node loss keeps the index available.
- HA (node loss → index stays green) is verified at the staging bring-up, alongside the syslog-ng field-shape check and the CRL (3.2-bis).

(Write the prose to match the README's existing voice; keep it a short, accurate subsection.)

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_multinode_config.py -v` → 2 passed.

- [ ] **Step 7: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add docker-compose.logs.multinode.yml deploy/opensearch/index-template.multinode.json backend/tests/test_multinode_config.py README.md
git commit -m "feat(logs): multi-node OpenSearch config (3-node cluster + replicated template)"
```

---

## Final verification

- [ ] **Backend:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Frontend:** `cd frontend && npm run build && npx vitest run` → all pass.
- [ ] **Security review:** dispatch `security-reviewer` (the tenant filter is still injected from the path in every PIT search; the `pit_id` exposed to the browser is opaque and length-capped and cannot widen scope; `LOG_VIEW` gating unchanged; no result-window guard removed any isolation control; multi-node OpenSearch stays internal-only). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge.

---

## Self-review notes (author)

- **Spec coverage:** PIT keep-alive + `build_search_body` PIT/search_after/tiebreaker (Task 1) ✓; `open_pit` + cursor `search_logs` + next_cursor on full page only (Task 2) ✓; `LogCursor` in/out, drop page + deep-paging guard, cursor round-trip (Task 3) ✓; frontend accumulate + Load more + cursor exhaustion (Task 4) ✓; multi-node compose (3 nodes, no single-node) + replicated template + ops docs + parse test (Task 5) ✓; tenant-isolation-preserved + opaque pit_id security ✓.
- **Type consistency:** `build_search_body(*, tenant_id, frm, to, query, device_id, size, pit_id, search_after=None)` (Task 1) called by `search_logs` (Task 2); `search_logs(settings, *, tenant_id, frm, to, query, device_id, size, cursor=None)` (Task 2) called + faked identically in the API + tests (Task 3); cursor dict shape `{pit_id, after}` consistent service↔`LogCursor` schema↔frontend `next_cursor`; `SearchResult.next_cursor` (Task 1/2) → `LogSearchOut.next_cursor` (Task 3) → UI `cursor` (Task 4).
- **Risk flags:** (a) `_shard_doc` requires the PIT block — Task 1's sort + Task 2's PIT always ship together; (b) the OpenSearch `_pit` response key is `pit_id` (OpenSearch) — `open_pit` falls back to `id`; (c) Task 4 removes the `result` state — Step 5 notes removing every `result` reference so tsc passes; (d) the multinode compose is a standalone ALTERNATIVE (not a merge overlay) to avoid the `discovery.type: single-node` merge conflict — documented in the file header + README.
