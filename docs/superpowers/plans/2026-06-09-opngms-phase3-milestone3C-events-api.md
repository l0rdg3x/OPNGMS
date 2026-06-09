# OPNGMS — Phase 3 / Milestone 3C: Events Query API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the ingested events (IDS + DNS) via tenant-scoped REST endpoints — a paginated list and a top-N aggregation — isolated per customer by Postgres RLS, closing Phase 3 end-to-end.

**Architecture:** Mirrors the 2C pattern. A new `app/api/events.py` router under `/api/tenants/{tenant_id}` exposes `GET /events` (filtered, paginated list) and `GET /events/top` (top-N by a whitelisted field). An `EventRepository` runs tenant-scoped raw SQL against the `events` hypertable (application-level `tenant_id` filter + RLS). The API connects as `opngms_app`, so RLS filters per customer exactly as for metrics/alerts.

**Tech Stack:** FastAPI async, SQLAlchemy 2.0 async, TimescaleDB (`events` hypertable), Pydantic v2, pytest + pytest-asyncio.

---

## Context for the implementer (read first)

Existing backend in `/home/l0rdg3x/coding/OPNGMS/backend`. The entire codebase is **in English** — write all new code, comments, docstrings, and messages **in English**. Phase 3A/3B are already in `main`.

- **Reference pattern (2C)**: `app/api/monitoring.py` (tenant-scoped router: `require_tenant(Action.DEVICE_VIEW)` + `get_session`, query params with defensive caps, `from`/`to` UTC normalization), `app/repositories/metric.py` (raw-SQL tenant-scoped repository with `MAX_POINTS` cap and `_to_points` mapping), `app/repositories/alert.py` (ORM repository), `app/schemas/metric.py`/`alert.py` (Pydantic out-schemas).
- **Events model**: `app/models/event.py` — `Event` hypertable, columns `time, device_id, source, event_key, tenant_id, category, src_ip, dst_ip, name, severity, action, attributes`. PK `(time, device_id, source, event_key)`. RLS is already enabled on `events` (3A) and `events` is in `TENANT_TABLES`.
- **Tenant context / RBAC**: `app/core/deps.py` (`require_tenant`, sets `app.current_tenant`), `app/core/rbac.py` (`Action.DEVICE_VIEW` granted to all tenant roles — reuse it, do not add a new Action).
- **Router registration**: `app/main.py` — `app.include_router(...)`.
- **Tests**: `tests/conftest.py` (fixtures `db_engine`, `two_tenants`, `api_client` owner, `app_role_api_client` real `opngms_app`; the `events` hypertable is created in `db_engine`). `tests/test_metric_repository.py` and `tests/test_monitoring_api.py`/`test_monitoring_rls_api.py` are the templates for repo tests, endpoint tests, and cross-tenant isolation tests. `tests/test_rls_isolation.py` already has `test_events_isolated_cross_tenant` (raw SQL).

**Test command** (from `backend/`):
```
TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" \
.venv/bin/python -m pytest -q
```
Current suite: **143 tests green**.

**Security note (top-N):** the `field` parameter of `/events/top` becomes a SQL column name (cannot be bound as a parameter). It **MUST** be validated against an allowlist of column names; never interpolate a raw user value. All other values stay bound parameters.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `app/schemas/event.py` | `EventOut`, `EventTopRow` | Create |
| `app/repositories/event.py` | `EventRepository` (list + top), `MAX_EVENTS`, `TOP_FIELDS` | Create |
| `app/api/events.py` | Router: `GET /events`, `GET /events/top` | Create |
| `app/main.py` | `include_router(events_router)` | Modify |
| `tests/test_event_repository.py` | repository list/top, tenant-scoped | Create |
| `tests/test_events_api.py` | endpoint happy-path + RBAC + field allowlist | Create |
| `tests/test_events_rls_api.py` | cross-tenant isolation via real `opngms_app` | Create |

---

## Task 1: Event schema + repository + `GET /events` list endpoint

**Files:**
- Create: `app/schemas/event.py`, `app/repositories/event.py`, `app/api/events.py`
- Modify: `app/main.py`
- Create: `tests/test_event_repository.py`

- [ ] **Step 1: Write the failing repository test**

Create `tests/test_event_repository.py`. Seed a few events (as owner) and verify the repository, under `SET ROLE opngms_app` + tenant context, returns them filtered and ordered (most recent first).
```python
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.core.db_roles import APP_ROLE
from app.repositories.event import EventRepository


async def _seed(db_engine, tenant_id, device_id):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner -> bypasses RLS
        for i, (src, name) in enumerate([("ids", "ET SCAN"), ("ids", "ET POLICY"), ("dns", "example.com")]):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip) "
                    "VALUES (:t, :d, :src, :k, :tid, :name, '10.0.0.5')"
                ),
                {"t": base + timedelta(minutes=i), "d": device_id, "src": src,
                 "k": f"k{i}", "tid": tenant_id, "name": name},
            )
        await s.commit()
    return base


async def test_list_returns_most_recent_first(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).list(
            source=None, device_id=None, frm=None, to=None, limit=100
        )
    assert [r.name for r in rows] == ["example.com", "ET POLICY", "ET SCAN"]  # DESC by time


async def test_list_filters_by_source(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).list(
            source="dns", device_id=None, frm=None, to=None, limit=100
        )
    assert [r.source for r in rows] == ["dns"]
    assert rows[0].name == "example.com"


async def test_list_respects_limit(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    await _seed(db_engine, tenant_a, device_id)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).list(
            source=None, device_id=None, frm=None, to=None, limit=2
        )
    assert len(rows) == 2  # the 2 most recent
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `... pytest tests/test_event_repository.py -v` → FAIL (`app.repositories.event` missing).

- [ ] **Step 3: Write the event schema**

Create `app/schemas/event.py`:
```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class EventOut(BaseModel):
    time: datetime
    device_id: uuid.UUID
    source: str
    category: str
    src_ip: str
    dst_ip: str
    name: str
    severity: str
    action: str
    attributes: dict


class EventTopRow(BaseModel):
    value: str
    count: int
```

- [ ] **Step 4: Write the repository**

Create `app/repositories/event.py`. Application-level `tenant_id` filter on top of RLS (defense in depth). `MAX_EVENTS` caps the list. Optional filters bound as parameters.
```python
import uuid
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.event import EventOut, EventTopRow

# Defensive cap on the number of rows returned by the event list.
MAX_EVENTS = 1000

# Whitelist of columns allowed for top-N aggregation. The `field` becomes a SQL
# column name (cannot be bound), so it MUST be validated against this set.
TOP_FIELDS = frozenset({"src_ip", "dst_ip", "name", "action", "severity"})

_LIST_COLUMNS = "time, device_id, source, category, src_ip, dst_ip, name, severity, action, attributes"


class EventRepository:
    """Tenant-scoped event reads. Double isolation: tenant_id filter + RLS."""

    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(
        self,
        *,
        source: str | None,
        device_id: uuid.UUID | None,
        frm: datetime | None,
        to: datetime | None,
        limit: int,
    ) -> list[EventOut]:
        clauses = ["tenant_id = :tid"]
        params: dict = {"tid": self.tenant_id, "limit": min(limit, MAX_EVENTS)}
        if source is not None:
            clauses.append("source = :source")
            params["source"] = source
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        if frm is not None:
            clauses.append("time >= :frm")
            params["frm"] = frm
        if to is not None:
            clauses.append("time < :to")
            params["to"] = to
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {_LIST_COLUMNS} FROM events WHERE {where} "
            "ORDER BY time DESC LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [EventOut(**dict(r)) for r in rows]

    async def top(
        self,
        *,
        field: str,
        source: str | None,
        frm: datetime | None,
        to: datetime | None,
        limit: int,
    ) -> list[EventTopRow]:
        if field not in TOP_FIELDS:
            raise ValueError(f"field not allowed: {field}")
        clauses = ["tenant_id = :tid", f"{field} <> ''"]
        params: dict = {"tid": self.tenant_id, "limit": min(limit, MAX_EVENTS)}
        if source is not None:
            clauses.append("source = :source")
            params["source"] = source
        if frm is not None:
            clauses.append("time >= :frm")
            params["frm"] = frm
        if to is not None:
            clauses.append("time < :to")
            params["to"] = to
        where = " AND ".join(clauses)
        # `field` is validated against TOP_FIELDS above (safe to interpolate).
        sql = text(
            f"SELECT {field} AS value, count(*) AS count FROM events WHERE {where} "
            f"GROUP BY {field} ORDER BY count DESC, value LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [EventTopRow(value=str(r.value), count=int(r.count)) for r in rows]
```

- [ ] **Step 5: Run the repository test and verify it passes**

Run: `... pytest tests/test_event_repository.py -v` → PASS (3/3).

- [ ] **Step 6: Write the list endpoint and register the router**

Create `app/api/events.py`:
```python
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.repositories.event import MAX_EVENTS, EventRepository
from app.schemas.event import EventOut

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["events"])


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@router.get("/events", response_model=list[EventOut])
async def list_events(
    tenant_id: uuid.UUID,
    source: str | None = Query(None),
    device_id: uuid.UUID | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(100, ge=1, le=MAX_EVENTS),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[EventOut]:
    repo = EventRepository(session, tenant_id)
    return await repo.list(
        source=source, device_id=device_id,
        frm=_ensure_utc(from_), to=_ensure_utc(to), limit=limit,
    )
```

In `app/main.py`, add the import and registration next to the other routers:
```python
from app.api.events import router as events_router
```
and after `app.include_router(monitoring_router)`:
```python
app.include_router(events_router)
```

- [ ] **Step 7: Run the whole suite**

Run: `... pytest -q` → all green (143 + the new repository tests). The endpoint is tested in Task 1 Step 8.

- [ ] **Step 8: Write the endpoint + isolation tests**

Create `tests/test_events_api.py` (owner client happy-path + RBAC) and `tests/test_events_rls_api.py` (cross-tenant isolation via real `opngms_app`), mirroring `tests/test_monitoring_api.py`/`test_monitoring_rls_api.py`. Cover:
- `GET /events` returns seeded events (owner client), filtered by `source`, capped by `limit`.
- 401 without a session cookie.
- 403 for a non-superadmin user without membership on the tenant (use `make_user` + login, like `test_monitoring_forbidden_without_membership`).
- Cross-tenant: insert events for two tenants (owner), then via `app_role_api_client` confirm tenant A's `GET /events` returns only A's events and not B's. Add a raw-SQL assertion (real `opngms_app` connection, no `tenant_id` filter, context = A → only A's rows) to prove RLS, not just the app filter (the lesson from 2C's isolation review).

Run: `... pytest tests/test_events_api.py tests/test_events_rls_api.py -v` → PASS.

- [ ] **Step 9: Run the whole suite + alembic check**

Run: `... pytest -q` → all green. `alembic check` on a clean DB → "No new upgrade operations detected" (3C adds no migrations).

- [ ] **Step 10: Commit**
```bash
git add app/schemas/event.py app/repositories/event.py app/api/events.py app/main.py \
        tests/test_event_repository.py tests/test_events_api.py tests/test_events_rls_api.py
git commit -m "feat(backend): events list API (repository + GET /events, tenant-scoped + RLS)"
```

---

## Task 2: `GET /events/top` aggregation endpoint

**Files:**
- Modify: `app/api/events.py`
- Modify: `tests/test_event_repository.py`, `tests/test_events_api.py`

- [ ] **Step 1: Write the failing repository test for `top`**

Append to `tests/test_event_repository.py`. Seed events with repeated `src_ip`/`name` and verify the top-N counts and ordering, and that a non-whitelisted `field` raises.
```python
import pytest


async def test_top_counts_by_field(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    device_id = uuid.uuid4()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    base = datetime(2026, 6, 9, 12, 0, tzinfo=timezone.utc)
    async with factory() as s:  # owner
        seed = [("1.1.1.1", "a"), ("1.1.1.1", "b"), ("2.2.2.2", "c")]
        for i, (ip, key) in enumerate(seed):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, src_ip, name) "
                    "VALUES (:t, :d, 'ids', :k, :tid, :ip, 'sig')"
                ),
                {"t": base, "d": device_id, "k": key, "tid": tenant_a, "ip": ip},
            )
        await s.commit()
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        rows = await EventRepository(s, tenant_a).top(
            field="src_ip", source=None, frm=None, to=None, limit=10
        )
    assert [(r.value, r.count) for r in rows] == [("1.1.1.1", 2), ("2.2.2.2", 1)]


async def test_top_rejects_non_whitelisted_field(db_engine, two_tenants):
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await s.execute(text(f"SET ROLE {APP_ROLE}"))
        await set_tenant_context(s, tenant_a)
        with pytest.raises(ValueError):
            await EventRepository(s, tenant_a).top(
                field="tenant_id; DROP TABLE events", source=None, frm=None, to=None, limit=10
            )
```

- [ ] **Step 2: Run and verify it fails**

Run: `... pytest tests/test_event_repository.py -k top -v` → FAIL (no `top` method).

(The `top` method is already written in Task 1 Step 4 — if you implemented the repository fully there, this test passes immediately, which is fine: it locks in the behavior. If you deferred `top`, implement it now per Task 1 Step 4.)

- [ ] **Step 3: Add the `/events/top` endpoint**

In `app/api/events.py`, add the import and endpoint:
```python
from fastapi import HTTPException
from app.repositories.event import TOP_FIELDS
from app.schemas.event import EventTopRow
```
```python
@router.get("/events/top", response_model=list[EventTopRow])
async def top_events(
    tenant_id: uuid.UUID,
    field: str = Query(..., description="Column to aggregate by"),
    source: str | None = Query(None),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    limit: int = Query(10, ge=1, le=100),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[EventTopRow]:
    if field not in TOP_FIELDS:
        raise HTTPException(status_code=400, detail=f"field must be one of {sorted(TOP_FIELDS)}")
    repo = EventRepository(session, tenant_id)
    return await repo.top(
        field=field, source=source, frm=_ensure_utc(from_), to=_ensure_utc(to), limit=limit,
    )
```

- [ ] **Step 4: Add the endpoint test (incl. allowlist 400)**

In `tests/test_events_api.py`, add a test that `GET /events/top?field=src_ip` returns ranked counts and that `field=bogus` (or an injection string) returns 400.

- [ ] **Step 5: Run and verify it passes**

Run: `... pytest tests/test_event_repository.py tests/test_events_api.py -v` → PASS. Then the whole suite green.

- [ ] **Step 6: Commit**
```bash
git add app/api/events.py tests/test_event_repository.py tests/test_events_api.py
git commit -m "feat(backend): events top-N aggregation API (GET /events/top, whitelisted field)"
```

---

## Task 3: Technical debt

- [ ] **Step 1: Record the 3C debt**

Append to this plan:
```markdown
## Technical debt (3C)

- **Offset/keyset pagination missing**: `GET /events` returns the most recent `limit` rows (cap
  `MAX_EVENTS`). For deep history, add keyset pagination (`before`/`after` cursor on `time`).
- **`top` over the full retention window**: aggregation scans raw rows; for large windows a
  TimescaleDB continuous aggregate (Phase 5) would be cheaper.
- **`field`/`source` not enumerated at the schema level**: validated against allowlists in code;
  consider typed enums for OpenAPI documentation and 422 validation.
- **`attributes` exposed raw in `EventOut`**: the full normalized record is returned; it is the
  tenant's own data (RLS-isolated), but consider trimming to needed fields for report endpoints.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase3-milestone3C-events-api.md
git commit -m "docs: technical debt milestone 3C"
```

---

## Definition of "Done" (3C, and Phase 3)
- `GET /events` returns a filtered, paginated, tenant-scoped event list (most recent first, capped).
- `GET /events/top` returns top-N counts by a whitelisted field (no SQL injection vector).
- Both endpoints are gated by `require_tenant(DEVICE_VIEW)` and isolated by RLS — a real `opngms_app`
  test proves cross-tenant isolation (app filter + RLS).
- Suite green + `alembic check` clean.
- **With 3C, Phase 3 is complete**: ingest (IDS + DNS) → storage → query API.

---

## Technical debt (3C) — consolidated from reviews

- **Offset/keyset pagination missing**: `GET /events` returns the most recent `limit` rows (cap
  `MAX_EVENTS=1000`). For deep history, add keyset pagination (`before`/`after` cursor on `time`).
- **`top` over the full retention window**: aggregation scans raw rows; for large windows a
  TimescaleDB continuous aggregate (Phase 5) would be cheaper.
- **`field`/`source` not enumerated at the schema level**: validated against allowlists in code, so
  an invalid value returns 400 (our check) rather than Pydantic 422. Consider typed enums for OpenAPI
  documentation and 422 validation.
- **`top` uses the list cap** (review Task 2): `EventRepository.top` clamps with `min(limit,
  MAX_EVENTS=1000)` instead of a dedicated top-N cap. Harmless because the endpoint enforces
  `le=100`, but a direct repo call could exceed 100. Cosmetic.
- **`attributes` exposed raw in `EventOut`**: the full normalized record is returned; it is the
  tenant's own data (RLS-isolated), but consider trimming to needed fields for report endpoints.
