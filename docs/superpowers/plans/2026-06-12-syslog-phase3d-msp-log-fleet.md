# Syslog Phase 3.4 — MSP Log-Fleet Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A superadmin-only cross-tenant "Log fleet" dashboard — per-tenant forwarding status, ingest health (last-log + silent flag), and 24h log volume.

**Architecture:** A new org-level `LOG_FLEET_VIEW` action gates a `GET /api/admin/log-fleet` endpoint. Forwarding counts come from a per-tenant RLS loop (list tenants, `set_tenant_context`, COUNT) — no bypass role. Volume + last-log come from ONE OpenSearch `terms` aggregation on `tenant_id` with **no tenant filter** (superadmin-only, aggregates only), best-effort. A React superadmin page renders cards + a per-tenant table.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy + Postgres RLS · httpx · OpenSearch aggregations · React 19 + Mantine v9 + react-query + openapi-fetch · pytest + respx · vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-06-12-syslog-phase3d-msp-log-fleet-design.md`
**Branch:** `feat/log-fleet-dashboard` (already created off main).

---

## File Structure

**Backend — create:** `app/services/log_fleet.py`, `app/schemas/log_fleet.py`, `app/api/log_fleet.py`, tests `tests/test_log_fleet_service.py`, `tests/test_log_fleet_api.py`.
**Backend — modify:** `app/core/rbac.py` (LOG_FLEET_VIEW), `app/main.py` (router).
**Frontend — create:** `frontend/src/logs/logFleetHooks.ts`, `frontend/src/pages/LogFleetPage.tsx`, `frontend/src/pages/__tests__/logFleet.test.tsx`.
**Frontend — modify:** `frontend/src/api/schema.d.ts` + `openapi.json` (regen), `frontend/src/components/AppShell.tsx` (nav + route), `frontend/src/i18n/en.ts` (label).

---

## Conventions
- Backend DB tests prefix `TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"`; pure/respx tests always run. Commit from REPO ROOT with `backend/...`/`frontend/...` paths. English everywhere; commit per task. Frontend PR gate: `npm run build`.

---

# PHASE A — backend

## Task 1: RBAC `LOG_FLEET_VIEW` + `fleet_forwarding_counts`

**Files:**
- Modify: `backend/app/core/rbac.py`
- Create: `backend/app/services/log_fleet.py`, `backend/tests/test_log_fleet_service.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_log_fleet_service.py`

```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.services.log_fleet import fleet_forwarding_counts


async def _seed_tenant(s, *, slug, enabled, revoked, disabled):
    tid = uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:sl,'active')"),
                    {"i": tid, "n": slug.upper(), "sl": slug})
    await set_tenant_context(s, tid)
    n = 0
    for _ in range(enabled):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
    for _ in range(revoked):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint,revoked_at) "
            "VALUES (:d,:t,false,'s','f',now())"), {"d": did, "t": tid})
    for _ in range(disabled):
        n += 1
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,:nm,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
            {"i": did, "t": tid, "nm": f"{slug}-{n}"})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,false,'s','f')"), {"d": did, "t": tid})
    return tid


async def test_fleet_forwarding_counts_per_tenant(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await _seed_tenant(s, slug="acme", enabled=2, revoked=1, disabled=0)
        tb = await _seed_tenant(s, slug="beta", enabled=1, revoked=0, disabled=1)
        await s.commit()
    async with factory() as s:
        counts = await fleet_forwarding_counts(s)
    assert counts[ta]["enabled"] == 2 and counts[ta]["revoked"] == 1 and counts[ta]["disabled"] == 0
    assert counts[ta]["total_devices"] == 3 and counts[ta]["tenant_name"] == "ACME"
    assert counts[tb]["enabled"] == 1 and counts[tb]["disabled"] == 1 and counts[tb]["revoked"] == 0
    assert counts[tb]["total_devices"] == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_log_fleet_service.py -v` → FAIL (ImportError).

- [ ] **Step 3: RBAC** — `backend/app/core/rbac.py`

Add to the org-level section of `Action` (near `TEMPLATE_MANAGE`):
```python
    LOG_FLEET_VIEW = "log_fleet.view"
```
Add it to the `_ORG_ACTIONS` set:
```python
_ORG_ACTIONS = {Action.TENANT_MANAGE, Action.USER_MANAGE, Action.TEMPLATE_MANAGE, Action.LOG_FLEET_VIEW}
```

- [ ] **Step 4: Implement** — `backend/app/services/log_fleet.py`

```python
"""Superadmin cross-tenant log-fleet aggregates (the only cross-tenant views in the console)."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import set_tenant_context
from app.models.device import Device
from app.models.device_log_forwarding import DeviceLogForwarding
from app.repositories.tenant import TenantRepository


async def fleet_forwarding_counts(session: AsyncSession) -> dict[uuid.UUID, dict]:
    """Per-tenant device-forwarding counts. Lists tenants (the tenants table is not RLS-scoped), then
    for each tenant sets the RLS context and counts — no bypass role. Returns {tenant_id: {...}}."""
    tenants = await TenantRepository(session).list()
    out: dict[uuid.UUID, dict] = {}
    for t in tenants:
        await set_tenant_context(session, t.id)
        rows = (await session.execute(
            select(DeviceLogForwarding.enabled, DeviceLogForwarding.revoked_at)
        )).all()
        enabled = sum(1 for e, _ in rows if e)
        revoked = sum(1 for e, r in rows if not e and r is not None)
        disabled = sum(1 for e, r in rows if not e and r is None)
        total_devices = (await session.execute(select(func.count()).select_from(Device))).scalar_one()
        out[t.id] = {
            "tenant_name": t.name,
            "enabled": enabled,
            "disabled": disabled,
            "revoked": revoked,
            "total_devices": int(total_devices),
        }
    return out
```

- [ ] **Step 5: Run to verify pass + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_log_fleet_service.py -v` → PASS.
Run: `cd backend && .venv/bin/ruff check app/services/log_fleet.py app/core/rbac.py` → clean.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/core/rbac.py backend/app/services/log_fleet.py backend/tests/test_log_fleet_service.py
git commit -m "feat(log-fleet): LOG_FLEET_VIEW RBAC + per-tenant forwarding counts (RLS loop)"
```

---

## Task 2: `fleet_log_stats` (OpenSearch agg) + `log_fleet_overview`

**Files:**
- Modify: `backend/app/services/log_fleet.py`
- Test: `backend/tests/test_log_fleet_service.py` (append)

- [ ] **Step 1: Append the failing tests** to `backend/tests/test_log_fleet_service.py`

```python
import httpx
import respx

from app.services.log_fleet import fleet_log_stats, log_fleet_overview


class _S:
    opensearch_url = "http://opensearch:9200"


_OS = "http://opensearch:9200/opngms-logs-*/_search"


@respx.mock
async def test_fleet_log_stats_maps_buckets():
    respx.post(_OS).mock(return_value=httpx.Response(200, json={"aggregations": {"by_tenant": {"buckets": [
        {"key": "tid-a", "doc_count": 9, "last_log": {"value_as_string": "2026-06-01T10:00:00.000Z"},
         "last_24h": {"doc_count": 4}},
    ]}}}))
    stats = await fleet_log_stats(_S())
    assert stats["tid-a"]["volume_24h"] == 4
    assert stats["tid-a"]["last_log_at"] == "2026-06-01T10:00:00.000Z"


@respx.mock
async def test_fleet_log_stats_empty_on_error():
    respx.post(_OS).mock(return_value=httpx.Response(503, json={}))
    assert await fleet_log_stats(_S()) == {}


async def test_log_fleet_overview_combines_and_flags_silent(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        ta = await _seed_tenant(s, slug="acme", enabled=2, revoked=0, disabled=0)  # forwarding, will be silent
        tb = await _seed_tenant(s, slug="beta", enabled=0, revoked=0, disabled=1)  # no forwarding
        await s.commit()

    async def fake_stats(settings):
        return {}  # OpenSearch returns nothing -> acme has enabled>0 + no last_log -> silent

    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    async with factory() as s:
        ov = await log_fleet_overview(s, _S())
    by_id = {r["tenant_id"]: r for r in ov["tenants"]}
    assert by_id[ta]["enabled"] == 2 and by_id[ta]["last_log_at"] is None
    assert ov["totals"]["tenants_with_forwarding"] == 1
    assert ov["totals"]["silent_tenants"] == 1
    assert ov["totals"]["enabled_devices"] == 2
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** — append to `backend/app/services/log_fleet.py`

Add imports at the top (with the others):
```python
from datetime import UTC, datetime, timedelta

import httpx
```
Append:
```python
STALE_AFTER = timedelta(hours=1)


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


async def fleet_log_stats(settings) -> dict[str, dict]:
    """Per-tenant {last_log_at, volume_24h} via ONE OpenSearch terms agg on tenant_id (NO tenant
    filter — superadmin-only, aggregates only). Best-effort: returns {} on any OpenSearch error."""
    body = {
        "size": 0,
        "aggs": {"by_tenant": {
            "terms": {"field": "tenant_id", "size": 1000},
            "aggs": {
                "last_log": {"max": {"field": "@timestamp"}},
                "last_24h": {"filter": {"range": {"@timestamp": {"gte": "now-24h"}}}},
            },
        }},
    }
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return {}
    out: dict[str, dict] = {}
    for b in data.get("aggregations", {}).get("by_tenant", {}).get("buckets", []):
        out[str(b.get("key", ""))] = {
            "last_log_at": (b.get("last_log", {}) or {}).get("value_as_string"),
            "volume_24h": (b.get("last_24h", {}) or {}).get("doc_count"),
        }
    return out


async def log_fleet_overview(session: AsyncSession, settings) -> dict:
    """Combine the relational forwarding counts with the OpenSearch log stats into per-tenant rows +
    totals. A tenant is 'silent' when it has enabled devices but no recent log."""
    counts = await fleet_forwarding_counts(session)
    stats = await fleet_log_stats(settings)
    now = datetime.now(UTC)
    rows: list[dict] = []
    silent = enabled_devices = volume_total = with_fwd = 0
    for tid, c in counts.items():
        st = stats.get(str(tid), {})
        last_dt = _parse_iso(st["last_log_at"]) if st.get("last_log_at") else None
        vol = st.get("volume_24h")
        rows.append({
            "tenant_id": tid, "tenant_name": c["tenant_name"],
            "enabled": c["enabled"], "disabled": c["disabled"], "revoked": c["revoked"],
            "total_devices": c["total_devices"], "last_log_at": last_dt, "volume_24h": vol,
        })
        enabled_devices += c["enabled"]
        volume_total += vol or 0
        if c["enabled"] > 0:
            with_fwd += 1
            if last_dt is None or (now - last_dt) > STALE_AFTER:
                silent += 1
    rows.sort(key=lambda r: r["tenant_name"])
    return {"tenants": rows, "totals": {
        "tenants_with_forwarding": with_fwd, "enabled_devices": enabled_devices,
        "volume_24h": volume_total, "silent_tenants": silent}}
```

- [ ] **Step 4: Run to verify pass + lint** (all tests pass; ruff clean on `app/services/log_fleet.py`).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/log_fleet.py backend/tests/test_log_fleet_service.py
git commit -m "feat(log-fleet): OpenSearch volume/last-log agg + overview combiner (silent flag)"
```

---

## Task 3: API + schemas

**Files:**
- Create: `backend/app/schemas/log_fleet.py`, `backend/app/api/log_fleet.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_log_fleet_api.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_log_fleet_api.py`

```python
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.factories import make_user

pytestmark = pytest.mark.asyncio


async def _seed_one_tenant(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'Acme','acme','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.execute(text(
            "INSERT INTO device_log_forwarding (device_id,tenant_id,enabled,cert_serial,cert_fingerprint) "
            "VALUES (:d,:t,true,'s','f')"), {"d": did, "t": tid})
        await s.commit()
    return tid


async def test_superadmin_sees_fleet(api_client, db_engine, monkeypatch):
    async def fake_stats(settings):
        return {}
    monkeypatch.setattr("app.services.log_fleet.fleet_log_stats", fake_stats)
    await _seed_one_tenant(db_engine)
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet")
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(t["tenant_name"] == "Acme" and t["enabled"] == 1 for t in body["tenants"])
    assert body["totals"]["enabled_devices"] >= 1


async def test_non_superadmin_denied(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="op@x.io", password="pw12345", is_superadmin=False)
        await s.commit()
    await api_client.post("/api/login", json={"email": "op@x.io", "password": "pw12345"})
    r = await api_client.get("/api/admin/log-fleet")
    assert r.status_code == 403
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Schemas** — `backend/app/schemas/log_fleet.py`

```python
import uuid
from datetime import datetime

from pydantic import BaseModel


class LogFleetRow(BaseModel):
    tenant_id: uuid.UUID
    tenant_name: str
    enabled: int
    disabled: int
    revoked: int
    total_devices: int
    last_log_at: datetime | None
    volume_24h: int | None


class LogFleetTotals(BaseModel):
    tenants_with_forwarding: int
    enabled_devices: int
    volume_24h: int
    silent_tenants: int


class LogFleetOut(BaseModel):
    tenants: list[LogFleetRow]
    totals: LogFleetTotals
```

- [ ] **Step 4: API** — `backend/app/api/log_fleet.py`

```python
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.deps import require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.log_fleet import LogFleetOut
from app.services.log_fleet import log_fleet_overview

router = APIRouter(prefix="/api/admin", tags=["log-fleet"])


@router.get("/log-fleet", response_model=LogFleetOut)
async def get_log_fleet(
    user: User = Depends(require_org(Action.LOG_FLEET_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> LogFleetOut:
    data = await log_fleet_overview(session, get_settings())
    return LogFleetOut(**data)
```
Mount in `backend/app/main.py`: `from app.api.log_fleet import router as log_fleet_router` + `app.include_router(log_fleet_router)`.

- [ ] **Step 5: Run to verify pass + lint**

Run: `cd backend && TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" .venv/bin/pytest tests/test_log_fleet_api.py -v` → 2 passed.
Run: `cd backend && .venv/bin/ruff check app/api/log_fleet.py app/schemas/log_fleet.py app/main.py` → clean.

- [ ] **Step 6: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/schemas/log_fleet.py backend/app/api/log_fleet.py backend/app/main.py backend/tests/test_log_fleet_api.py
git commit -m "feat(log-fleet): superadmin cross-tenant log-fleet API (LOG_FLEET_VIEW)"
```

---

# PHASE B — frontend

## Task 4: Log-fleet superadmin page

**Files:**
- Modify: `frontend/src/api/schema.d.ts` + `openapi.json` (regen), `frontend/src/components/AppShell.tsx`, `frontend/src/i18n/en.ts`
- Create: `frontend/src/logs/logFleetHooks.ts`, `frontend/src/pages/LogFleetPage.tsx`, `frontend/src/pages/__tests__/logFleet.test.tsx`

- [ ] **Step 1: Regenerate the client**

Run: `cd frontend && npm run gen:api` then `grep -c "log-fleet" src/api/schema.d.ts` → > 0.

- [ ] **Step 2: Write the failing test** — `frontend/src/pages/__tests__/logFleet.test.tsx`

```tsx
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";

import { LogFleetPage } from "../LogFleetPage";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const FLEET = "http://localhost:3000/api/admin/log-fleet";

describe("LogFleetPage", () => {
  it("renders totals + per-tenant rows and flags a silent tenant", async () => {
    server.use(http.get(FLEET, () => HttpResponse.json({
      tenants: [
        { tenant_id: "a", tenant_name: "Acme", enabled: 2, disabled: 0, revoked: 0,
          total_devices: 2, last_log_at: null, volume_24h: null },          // silent (enabled, no log)
        { tenant_id: "b", tenant_name: "Beta", enabled: 1, disabled: 1, revoked: 0,
          total_devices: 2, last_log_at: "2026-06-01T10:00:00Z", volume_24h: 42 },
      ],
      totals: { tenants_with_forwarding: 2, enabled_devices: 3, volume_24h: 42, silent_tenants: 1 },
    })));
    renderWithProviders(<LogFleetPage />);
    expect(await screen.findByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    expect(screen.getByTestId("fleet-silent-count")).toHaveTextContent("1");
    // the silent tenant row carries a silent badge
    expect(screen.getByTestId("fleet-silent-a")).toBeInTheDocument();
    expect(screen.queryByTestId("fleet-silent-b")).toBeNull();
  });
});
```

- [ ] **Step 3: Run to verify it fails** (`cd frontend && npm test -- logFleet`).

- [ ] **Step 4: Hook** — `frontend/src/logs/logFleetHooks.ts`

```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type LogFleetOut = components["schemas"]["LogFleetOut"];

export function useLogFleet() {
  return useQuery({
    queryKey: ["log-fleet"],
    queryFn: async (): Promise<LogFleetOut> => {
      const { data, error } = await api.GET("/api/admin/log-fleet");
      if (error || !data) throw new Error("log fleet failed");
      return data;
    },
  });
}
```

- [ ] **Step 5: Page** — `frontend/src/pages/LogFleetPage.tsx`

```tsx
import { Alert, Badge, Card, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";

import { useLogFleet } from "../logs/logFleetHooks";

function isSilent(enabled: number, lastLogAt: string | null): boolean {
  if (enabled <= 0) return false;
  if (!lastLogAt) return true;
  return Date.now() - new Date(lastLogAt).getTime() > 60 * 60 * 1000; // > 1h
}

function StatCard({ label, value, testid }: { label: string; value: number; testid?: string }) {
  return (
    <Card withBorder padding="md" radius="md">
      <Text size="xs" c="dimmed">{label}</Text>
      <Text size="xl" fw={700} data-testid={testid}>{value}</Text>
    </Card>
  );
}

export function LogFleetPage() {
  const fleet = useLogFleet();
  if (fleet.isLoading) return <Loader />;
  if (fleet.isError || !fleet.data) return <Alert color="red">Failed to load the log fleet.</Alert>;
  const { tenants, totals } = fleet.data;

  return (
    <Stack>
      <Title order={3}>Log fleet</Title>
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard label="Tenants forwarding" value={totals.tenants_with_forwarding} />
        <StatCard label="Enabled devices" value={totals.enabled_devices} />
        <StatCard label="Volume (24h)" value={totals.volume_24h} />
        <StatCard label="Silent tenants" value={totals.silent_tenants} testid="fleet-silent-count" />
      </SimpleGrid>

      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Tenant</Table.Th><Table.Th>Forwarding</Table.Th><Table.Th>Revoked</Table.Th>
            <Table.Th>Last log</Table.Th><Table.Th>Volume 24h</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {tenants.map((t) => (
            <Table.Tr key={t.tenant_id}>
              <Table.Td>
                <Group gap="xs">
                  {t.tenant_name}
                  {isSilent(t.enabled, t.last_log_at ?? null) && (
                    <Badge color="red" variant="light" data-testid={`fleet-silent-${t.tenant_id}`}>silent</Badge>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>{t.enabled} / {t.total_devices}</Table.Td>
              <Table.Td>{t.revoked}</Table.Td>
              <Table.Td>{t.last_log_at ?? "—"}</Table.Td>
              <Table.Td>{t.volume_24h ?? "—"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}
```

- [ ] **Step 6: Nav + route** — `frontend/src/components/AppShell.tsx`

Lazy-import `LogFleetPage` (named-export form, like the other admin pages), add a superadmin-gated nav item next to the other `me?.is_superadmin && <NavItem to="/admin/…">` items:
```tsx
      {me?.is_superadmin && (
        <NavItem to="/admin/log-fleet" label={t.nav.logFleet} icon={<IconLogs />} />
      )}
```
and a route:
```tsx
              <Route path="/admin/log-fleet" element={<LogFleetPage />} />
```
Add `nav.logFleet: "Log fleet"` to `frontend/src/i18n/en.ts`. (Reuse an existing icon import such as `IconLogs`; do not invent a new icon.)

- [ ] **Step 7: Verify + build gate**

Run: `cd frontend && npm test -- logFleet && npm run build` → both pass.

- [ ] **Step 8: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/logs/logFleetHooks.ts frontend/src/pages/LogFleetPage.tsx frontend/src/pages/__tests__/logFleet.test.tsx frontend/src/components/AppShell.tsx frontend/src/i18n/en.ts
git commit -m "feat(log-fleet): superadmin cross-tenant log-fleet dashboard page"
```

---

## Final verification

- [ ] **Backend:** `cd backend && TEST_DATABASE_URL=… .venv/bin/pytest -q` → all pass; `ruff check app` clean.
- [ ] **Frontend:** `cd frontend && npm run build && npx vitest run` → all pass.
- [ ] **Security review:** dispatch `security-reviewer` (the OpenSearch agg is the ONLY no-tenant-filter query, strictly `LOG_FLEET_VIEW`/superadmin, aggregates-only — no raw cross-tenant log content; the relational loop sets RLS context per tenant, no bypass role; non-superadmin → 403; no secrets in the response). Address BLOCKER/IMPORTANT.
- [ ] **Finish:** `superpowers:finishing-a-development-branch` → PR with green CI, merge.

---

## Self-review notes (author)

- **Spec coverage:** `LOG_FLEET_VIEW` org action (Task 1) ✓; per-tenant RLS-loop forwarding counts (Task 1) ✓; OpenSearch no-filter volume/last-log agg, best-effort (Task 2) ✓; overview combiner + silent flag + totals (Task 2) ✓; superadmin-gated API + schemas + mount (Task 3) ✓; frontend cards + table + silent badge + null-ingest "—" + superadmin nav/route (Task 4) ✓; security (only-no-filter-is-OpenSearch-superadmin-aggregates; relational loop RLS-scoped) ✓; tests at every layer ✓.
- **Type consistency:** `fleet_forwarding_counts -> {tid: {tenant_name, enabled, disabled, revoked, total_devices}}` (Task 1) consumed by `log_fleet_overview` (Task 2); `fleet_log_stats -> {tid_str: {last_log_at, volume_24h}}` (Task 2) consumed by the combiner; `log_fleet_overview -> {tenants:[...], totals:{...}}` (Task 2) → `LogFleetOut(**data)` (Task 3) with matching `LogFleetRow`/`LogFleetTotals` fields → frontend `LogFleetOut` type (Task 4).
- **Risk flags:** (a) the relational loop re-applies `set_config(local=true)` per tenant within one request transaction — correct as long as no commit splits it (the read endpoint never commits); (b) `make_user(is_superadmin=…)` + `/api/setup` is the superadmin seeding pattern (Task 3 uses `/api/setup`); (c) the frontend page is data-driven and server-gated (the nav is superadmin-only) — the test renders it directly with mocked data.
