# OPNGMS — Phase 2 / Milestone 2D: Dashboard Frontend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A monitoring dashboard (React/Mantine) that consumes the 2C endpoints (`/metrics`, `/health`, `/alerts`): per-customer fleet overview, per-device health with charts, and an alerts page with active/historical filter.

**Architecture:** Extends the Milestone D frontend (Vite + React 19 + Mantine v9 + React Router + TanStack Query + typed `openapi-fetch` client). Adds a feature folder `src/monitoring/` (per-endpoint hooks, time-range utils, chart/card components) and three pages (new Overview as landing, extended DeviceDetail, new Alerts), with reorganised routing. Charts via `@mantine/charts` (on Recharts). Tests Vitest + RTL + MSW.

**Tech Stack:** React 19, Mantine v9 (`@mantine/core` + new `@mantine/charts`), `recharts`, TanStack Query v5, React Router v7, `openapi-fetch`, Vitest + Testing Library + MSW.

---

## Context for the implementer (read before starting)

Existing frontend codebase at `/home/l0rdg3x/coding/OPNGMS/frontend`. **Follow existing patterns.**

- **Typed API client** (`src/api/client.ts`): singleton `api` (`openapi-fetch`), already with CSRF middleware and `credentials:include`. Usage: `api.GET("/api/tenants/{tenant_id}/...", { params: { path: {...}, query: {...} } })` → returns `{ data, error }`. Types come from `src/api/schema.d.ts` (**must be regenerated**, Task 1).
- **Tenant context** (`src/tenant/TenantProvider.tsx`, `useTenant.ts`): `useTenant()` → `{ tenants, activeId, setActiveId, loading }`. `activeId` is the current tenant (string|null). Data hooks must be `enabled: !!activeId`.
- **Query pattern** (see `src/pages/DeviceDetailPage.tsx`): `useQuery({ queryKey: ["device", activeId, deviceId], enabled: !!activeId && !!deviceId, queryFn: async () => { const {data} = await api.GET(...); return data; } })`.
- **AppShell** (`src/components/AppShell.tsx`): header (TenantSwitcher + logout) + navbar (currently a single `NavLink` "Devices" → `/`) + `<Routes>` inside `MantineAppShell.Main`. Currently: `/`=`DevicesPage`, `/devices/:deviceId`=`DeviceDetailPage`.
- **Tests** (`src/test/utils.tsx`): `renderWithProviders(ui, { route })` wraps in `MantineProvider` + `QueryClientProvider` (retry:false) + `MemoryRouter`. The tenant is injected by wrapping in `<TenantContext.Provider value={{tenants, activeId, setActiveId, loading}}>` (see `src/pages/__tests__/devicedetail.test.tsx`, helper `withTenant`). MSW: `server.use(http.get("/api/tenants/t1/...", () => HttpResponse.json(...)))`. **`onUnhandledRequest: "error"`** (`src/test/setup.ts`): every endpoint called by a page MUST be mocked, otherwise the test fails.
- **Mantine CSS** imported in `src/main.tsx`: `import "@mantine/charts/styles.css"` must be added.

**Commands** (from `frontend/` dir):
- Test: `npm test` (vitest run). Existing tests are currently green.
- Lint/typecheck: `npm run lint` and `npm run build` (`tsc -b && vite build`).
- API type regeneration: `npm run gen:api` — **requires backend env vars** (imports `app.main`): run as
  `SESSION_SECRET=x MASTER_KEY="$(cd ../backend && .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms npm run gen:api`.

**Metric names** (confirmed in `backend/app/services/monitoring.py`): `cpu.pct`, `mem.pct`, `disk.pct`, `uptime.seconds`, `iface.bytes_in`, `iface.bytes_out`, `iface.up`, `gateway.rtt_ms`, `gateway.loss_pct`, `gateway.up`, `vpn.up`.

**Chart testing strategy (important):** Mantine Charts uses Recharts `ResponsiveContainer`, which in jsdom has no dimensions → does not reliably render SVG paths. Therefore: (1) data transformation logic is a **pure function tested separately**; (2) chart/page component tests assert **text/structures** (titles, values from `/health` and `/alerts`, empty-state), **not** SVG paths; (3) Task 1 adds a `ResizeObserver` mock in `setup.ts` as a safety net.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `package.json` / lockfile | Adds `@mantine/charts` + `recharts` | Modify (via npm i) |
| `src/api/schema.d.ts` | Regenerated types (includes 2C endpoints) | Regen |
| `src/main.tsx` | Import `@mantine/charts/styles.css` | Modify |
| `src/test/setup.ts` | Mock `ResizeObserver` | Modify |
| `src/monitoring/range.ts` | `rangeToParams(range, now)` (pure util) | Create |
| `src/monitoring/types.ts` | Local types (`MetricPoint`, `Range`) derived from schema | Create |
| `src/monitoring/hooks.ts` | `useTenantHealth`, `useAlerts`, `useDeviceMetrics` | Create |
| `src/monitoring/MetricChart.tsx` | Chart wrapper + `toChartData` (pure) | Create |
| `src/monitoring/HealthSummaryCards.tsx` | Summary cards from `/health` | Create |
| `src/pages/OverviewPage.tsx` | Landing: health + active alerts | Create |
| `src/pages/AlertsPage.tsx` | Alerts table + active/historical filter | Create |
| `src/pages/DeviceDetailPage.tsx` | + health section (charts + time-range) | Modify |
| `src/components/AppShell.tsx` | Routing + navbar (Overview/Devices/Alerts) | Modify |
| `src/monitoring/__tests__/*.test.tsx(ts)` | Unit/component tests | Create |
| `src/pages/__tests__/*.test.tsx` | Page tests | Create |

---

## Task 1: Data layer (install charts, schema, hooks, time-range util, test infra)

**Files:**
- Modify: `package.json` (npm i), `src/main.tsx`, `src/test/setup.ts`
- Regen: `src/api/schema.d.ts`
- Create: `src/monitoring/range.ts`, `src/monitoring/types.ts`, `src/monitoring/hooks.ts`
- Create: `src/monitoring/__tests__/range.test.ts`

- [ ] **Step 1: Install the chart library**

From `frontend/` dir:
```bash
npm i @mantine/charts recharts
```
Verify that `@mantine/charts` and `recharts` appear in `package.json` → `dependencies`.

- [ ] **Step 2: Regenerate API types (includes 2C endpoints)**

```bash
SESSION_SECRET=x \
MASTER_KEY="$(cd ../backend && .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
npm run gen:api
```
Verify that `src/api/schema.d.ts` now contains the paths `/api/tenants/{tenant_id}/devices/{device_id}/metrics`, `/api/tenants/{tenant_id}/health`, `/api/tenants/{tenant_id}/alerts` (e.g. `grep -c "/health" src/api/schema.d.ts`). If the command fails due to missing env vars, add them as for the backend tests.

- [ ] **Step 3: Import chart CSS**

In `src/main.tsx`, after `import "@mantine/notifications/styles.css";` add:
```ts
import "@mantine/charts/styles.css";
```

- [ ] **Step 4: Add ResizeObserver mock to tests**

In `src/test/setup.ts`, after the `matchMedia` block, add:
```ts
// Recharts ResponsiveContainer (used by @mantine/charts) observes dimensions via
// ResizeObserver, which is absent in jsdom. No-op mock: chart tests assert data/structures,
// not dimensions.
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;
```

- [ ] **Step 5: Time-range util — write the failing test**

Create `src/monitoring/__tests__/range.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { rangeToParams } from "../range";

describe("rangeToParams", () => {
  const now = new Date("2026-06-09T12:00:00.000Z");

  it("1h → 1h window, bucket 60s", () => {
    const p = rangeToParams("1h", now);
    expect(p.to).toBe("2026-06-09T12:00:00.000Z");
    expect(p.from).toBe("2026-06-09T11:00:00.000Z");
    expect(p.bucket).toBe(60);
  });

  it("24h → 24h window, bucket 300s", () => {
    const p = rangeToParams("24h", now);
    expect(p.from).toBe("2026-06-08T12:00:00.000Z");
    expect(p.bucket).toBe(300);
  });

  it("7d → 7-day window, bucket 3600s", () => {
    const p = rangeToParams("7d", now);
    expect(p.from).toBe("2026-06-02T12:00:00.000Z");
    expect(p.bucket).toBe(3600);
  });
});
```

- [ ] **Step 6: Run the test and verify it fails**

Run: `npm test -- range`
Expected: FAIL (module `../range` does not exist).

- [ ] **Step 7: Implement util + types**

Create `src/monitoring/types.ts`:
```ts
export type Range = "1h" | "24h" | "7d";

// Shape of a series point as returned by GET .../metrics (see MetricPoint backend).
export interface MetricPoint {
  time: string;
  label: string;
  value: number;
}
```

Create `src/monitoring/range.ts`:
```ts
import type { Range } from "./types";

const SPAN_SECONDS: Record<Range, number> = { "1h": 3600, "24h": 86400, "7d": 604800 };
const BUCKET_SECONDS: Record<Range, number> = { "1h": 60, "24h": 300, "7d": 3600 };

export interface RangeParams {
  from: string;
  to: string;
  bucket: number;
}

/** Converts a range preset to query params for the metrics endpoint.
 *  Bucket chosen to stay under MAX_POINTS (5000) on the API side and produce smooth charts. */
export function rangeToParams(range: Range, now: Date): RangeParams {
  const to = now;
  const from = new Date(now.getTime() - SPAN_SECONDS[range] * 1000);
  return { from: from.toISOString(), to: to.toISOString(), bucket: BUCKET_SECONDS[range] };
}
```

- [ ] **Step 8: Run the test and verify it passes**

Run: `npm test -- range`
Expected: PASS (3/3).

- [ ] **Step 9: Implement data hooks**

Create `src/monitoring/hooks.ts`:
```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import { rangeToParams } from "./range";
import type { Range } from "./types";

export function useTenantHealth() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["health", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/health", {
        params: { path: { tenant_id: activeId! } },
      });
      return data;
    },
  });
}

export function useAlerts(active: boolean) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["alerts", activeId, active],
    enabled: !!activeId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/alerts", {
        params: { path: { tenant_id: activeId! }, query: { active } },
      });
      return data ?? [];
    },
  });
}

export function useDeviceMetrics(deviceId: string | undefined, metric: string, range: Range) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["metrics", activeId, deviceId, metric, range],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { from, to, bucket } = rangeToParams(range, new Date());
      const { data } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/metrics",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId! },
            query: { metric, from, to, bucket },
          },
        },
      );
      return data;
    },
  });
}
```
**Note:** the exact query param names (`from`/`to`/`bucket`/`active`) and types come from the regenerated `schema.d.ts`. If TypeScript reports a mismatch on a parameter name, align to the real name in the schema (the backend endpoint uses aliases `from`/`bucket`). Run `npm run build` for the typecheck.

- [ ] **Step 10: Typecheck + suite**

Run: `npm run build` (tsc) — no type errors on the hooks.
Run: `npm test` — all tests green (existing + `range`).

- [ ] **Step 11: Commit**
```bash
git add package.json package-lock.json src/main.tsx src/test/setup.ts src/api/schema.d.ts src/monitoring/
git commit -m "feat(fe): data layer 2D (charts install, 2C schema, metrics/health/alerts hooks, range util)"
```

---

## Task 2: Base components — `MetricChart` + `HealthSummaryCards`

**Files:**
- Create: `src/monitoring/MetricChart.tsx`, `src/monitoring/HealthSummaryCards.tsx`
- Create: `src/monitoring/__tests__/metricchart.test.tsx`, `src/monitoring/__tests__/healthcards.test.tsx`

- [ ] **Step 1: `toChartData` test + `MetricChart` smoke test (failing)**

Create `src/monitoring/__tests__/metricchart.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { MetricChart, toChartData } from "../MetricChart";
import type { MetricPoint } from "../types";

const points: MetricPoint[] = [
  { time: "2026-06-09T12:00:00Z", label: "", value: 10 },
  { time: "2026-06-09T12:05:00Z", label: "", value: 20 },
];

describe("toChartData", () => {
  it("groups by timestamp with one series per label", () => {
    const multi: MetricPoint[] = [
      { time: "t1", label: "igb0", value: 1 },
      { time: "t1", label: "igb1", value: 2 },
      { time: "t2", label: "igb0", value: 3 },
    ];
    const { data, series } = toChartData(multi);
    expect(series).toEqual(["igb0", "igb1"]);
    expect(data).toEqual([
      { time: "t1", igb0: 1, igb1: 2 },
      { time: "t2", igb0: 3 },
    ]);
  });

  it("empty label → series 'value'", () => {
    const { series } = toChartData(points);
    expect(series).toEqual(["value"]);
  });
});

describe("MetricChart", () => {
  it("shows the title and does not crash with data", () => {
    render(
      <MantineProvider>
        <MetricChart title="CPU %" points={points} />
      </MantineProvider>,
    );
    expect(screen.getByText("CPU %")).toBeInTheDocument();
  });

  it("shows empty-state with no data", () => {
    render(
      <MantineProvider>
        <MetricChart title="CPU %" points={[]} />
      </MantineProvider>,
    );
    expect(screen.getByText(/no data/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run and verify the failure**

Run: `npm test -- metricchart`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Implement `MetricChart`**

Create `src/monitoring/MetricChart.tsx`:
```tsx
import { Card, Text } from "@mantine/core";
import { LineChart } from "@mantine/charts";
import type { MetricPoint } from "./types";

export interface ChartData {
  data: Record<string, number | string>[];
  series: string[];
}

/** Transforms {time,label,value} points into rows by timestamp with one column per label.
 *  Empty label ('') → column 'value'. Pure function, tested separately. */
export function toChartData(points: MetricPoint[]): ChartData {
  const seriesSet: string[] = [];
  const byTime = new Map<string, Record<string, number | string>>();
  for (const p of points) {
    const key = p.label === "" ? "value" : p.label;
    if (!seriesSet.includes(key)) seriesSet.push(key);
    let row = byTime.get(p.time);
    if (!row) {
      row = { time: p.time };
      byTime.set(p.time, row);
    }
    row[key] = p.value;
  }
  return { data: Array.from(byTime.values()), series: seriesSet };
}

const PALETTE = ["blue.6", "teal.6", "orange.6", "grape.6", "red.6", "cyan.6"];

export function MetricChart({
  title,
  points,
  unit,
}: {
  title: string;
  points: MetricPoint[];
  unit?: string;
}) {
  const { data, series } = toChartData(points);
  return (
    <Card withBorder padding="sm">
      <Text fw={600} size="sm" mb="xs">
        {title}
        {unit ? ` (${unit})` : ""}
      </Text>
      {data.length === 0 ? (
        <Text c="dimmed" size="sm">
          No data yet
        </Text>
      ) : (
        <LineChart
          h={200}
          data={data}
          dataKey="time"
          series={series.map((name, i) => ({ name, color: PALETTE[i % PALETTE.length] }))}
          curveType="monotone"
          withDots={false}
          tickLine="x"
        />
      )}
    </Card>
  );
}
```

- [ ] **Step 4: Run and verify the pass**

Run: `npm test -- metricchart`
Expected: PASS. (If `LineChart` throws in jsdom despite the ResizeObserver mock, wrap the chart render so the empty path remains testable; but with the mock from Step 1.4 the render should not crash — we only assert the title, not SVG paths.)

- [ ] **Step 5: `HealthSummaryCards` test (failing)**

Create `src/monitoring/__tests__/healthcards.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { HealthSummaryCards } from "../HealthSummaryCards";

describe("HealthSummaryCards", () => {
  it("shows total devices, per-status counts, and active alerts", () => {
    render(
      <MantineProvider>
        <HealthSummaryCards
          health={{ total_devices: 3, by_status: { reachable: 2, unverified: 1 }, active_alerts: 4 }}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("3")).toBeInTheDocument(); // total
    expect(screen.getByText(/reachable/i)).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // active alerts
  });
});
```

- [ ] **Step 6: Run and verify the failure**

Run: `npm test -- healthcards` → FAIL.

- [ ] **Step 7: Implement `HealthSummaryCards`**

Create `src/monitoring/HealthSummaryCards.tsx`:
```tsx
import { Card, Group, SimpleGrid, Text, Title } from "@mantine/core";

export interface FleetHealth {
  total_devices: number;
  by_status: Record<string, number>;
  active_alerts: number;
}

export function HealthSummaryCards({ health }: { health: FleetHealth }) {
  return (
    <SimpleGrid cols={{ base: 1, sm: 3 }}>
      <Card withBorder>
        <Text size="sm" c="dimmed">Total devices</Text>
        <Title order={2}>{health.total_devices}</Title>
        <Group gap="xs" mt="xs">
          {Object.entries(health.by_status).map(([status, count]) => (
            <Text key={status} size="sm">
              {status}: <b>{count}</b>
            </Text>
          ))}
        </Group>
      </Card>
      <Card withBorder>
        <Text size="sm" c="dimmed">Active alerts</Text>
        <Title order={2}>{health.active_alerts}</Title>
      </Card>
    </SimpleGrid>
  );
}
```

- [ ] **Step 8: Run and verify the pass**

Run: `npm test -- healthcards` → PASS. Then `npm test` full suite → green.

- [ ] **Step 9: Commit**
```bash
git add src/monitoring/MetricChart.tsx src/monitoring/HealthSummaryCards.tsx src/monitoring/__tests__/
git commit -m "feat(fe): MetricChart (+ toChartData) and HealthSummaryCards components"
```

---

## Task 3: `OverviewPage` + routing/navbar reorganisation

**Files:**
- Create: `src/pages/OverviewPage.tsx`, `src/pages/__tests__/overview.test.tsx`
- Modify: `src/components/AppShell.tsx`

- [ ] **Step 1: `OverviewPage` test (failing)**

Create `src/pages/__tests__/overview.test.tsx`:
```tsx
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { OverviewPage } from "../OverviewPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

describe("OverviewPage", () => {
  it("shows health and active alerts", async () => {
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 2, by_status: { reachable: 2 }, active_alerts: 1 }),
      ),
      http.get("/api/tenants/t1/alerts", () =>
        HttpResponse.json([
          {
            id: "a1", device_id: "d1", type: "device.down", label: "", severity: "critical",
            opened_at: "2026-06-09T10:00:00Z", resolved_at: null, details: {},
          },
        ]),
      ),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText("2")).toBeInTheDocument();
    expect(await screen.findByText(/device\.down/)).toBeInTheDocument();
  });

  it("empty-state with no active alerts", async () => {
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText(/no active alerts/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run and verify the failure**

Run: `npm test -- overview` → FAIL.

- [ ] **Step 3: Implement `OverviewPage`**

Create `src/pages/OverviewPage.tsx`:
```tsx
import { Alert, Badge, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useAlerts, useTenantHealth } from "../monitoring/hooks";
import { HealthSummaryCards, type FleetHealth } from "../monitoring/HealthSummaryCards";

export function OverviewPage() {
  const health = useTenantHealth();
  const alerts = useAlerts(true);

  return (
    <Stack>
      <Title order={3}>Overview</Title>
      {health.isLoading && <Loader />}
      {health.error && <Alert color="red">Error loading fleet health</Alert>}
      {health.data && <HealthSummaryCards health={health.data as FleetHealth} />}

      <Title order={4} mt="md">Active alerts</Title>
      {alerts.isLoading && <Loader />}
      {alerts.data && alerts.data.length === 0 && (
        <Text c="dimmed">No active alerts</Text>
      )}
      {alerts.data && alerts.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Type</Table.Th>
              <Table.Th>Label</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Opened</Table.Th>
              <Table.Th>Device</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {alerts.data.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>{a.type}</Table.Td>
                <Table.Td>{a.label || "—"}</Table.Td>
                <Table.Td><Badge color={a.severity === "critical" ? "red" : "yellow"}>{a.severity}</Badge></Table.Td>
                <Table.Td>{new Date(a.opened_at).toLocaleString()}</Table.Td>
                <Table.Td><Link to={`/devices/${a.device_id}`}>{a.device_id.slice(0, 8)}</Link></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
```
**Type note:** `alerts.data`/`health.data` are typed by the generated schema. If the generated types differ (e.g. `details` optional), adapt the accesses; avoid `any` where possible (`as FleetHealth` casts are acceptable where the schema is wider).

- [ ] **Step 4: Run and verify the pass**

Run: `npm test -- overview` → PASS (2/2).

- [ ] **Step 5: Reorganise routing + navbar in `AppShell`**

In `src/components/AppShell.tsx`:
- import `OverviewPage` and `AlertsPage` (the latter created in Task 5; if AlertsPage does not exist yet, do NOT import it — add its route in Task 5. In this task add only Overview + move Devices).
- Additional imports: `OverviewPage` from `../pages/OverviewPage`.

Replace the navbar block:
```tsx
<MantineAppShell.Navbar p="sm">
  <NavLink component={RouterNavLink} to="/" label="Overview" />
  <NavLink component={RouterNavLink} to="/devices" label="Devices" />
  <NavLink component={RouterNavLink} to="/alerts" label="Alerts" />
</MantineAppShell.Navbar>
```
and the Routes block:
```tsx
<Routes>
  <Route path="/" element={<OverviewPage />} />
  <Route path="/devices" element={<DevicesPage />} />
  <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
</Routes>
```
(The `/alerts` route is added in Task 5.)

**Update internal links** that pointed to `/` for the device list: search for `to="/"` / `navigate("/")` in components/pages (e.g. after device creation/deletion) and redirect to `/devices` where the intent was "go back to device list". Run:
```bash
grep -rn '"/"' src --include=*.tsx | grep -v OverviewPage
```
and fix cases that meant the device list (e.g. in `DeviceActions`/`DevicesPage`/delete tests that expect "home"). Update impacted tests accordingly (e.g. `devicedetail.test.tsx` delete → the return route).

- [ ] **Step 6: Typecheck + full suite**

Run: `npm run build` → no errors.
Run: `npm test` → all green (update any tests that depended on `/`=Devices).

- [ ] **Step 7: Commit**
```bash
git add src/pages/OverviewPage.tsx src/pages/__tests__/overview.test.tsx src/components/AppShell.tsx
git commit -m "feat(fe): OverviewPage (health + active alerts) + reorganised routing/navbar"
```

---

## Task 4: Extended `DeviceDetailPage` — health section with charts

**Files:**
- Modify: `src/pages/DeviceDetailPage.tsx`
- Create: `src/monitoring/DeviceHealthSection.tsx`
- Modify/Create: `src/pages/__tests__/devicedetail.test.tsx` (add health tests)

- [ ] **Step 1: Health section test (failing)**

Add a new test to `src/pages/__tests__/devicedetail.test.tsx` (keep existing ones). Mock the metrics endpoint with a handler that inspects the `metric` query param and returns a series; also mock the GET device:
```tsx
it("shows the health section with charts and range selector", async () => {
  server.use(
    http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
    http.get("/api/tenants/t1/devices/d1/metrics", ({ request }) => {
      const url = new URL(request.url);
      const metric = url.searchParams.get("metric");
      return HttpResponse.json({
        metric,
        points: [
          { time: "2026-06-09T12:00:00Z", label: "", value: 12 },
          { time: "2026-06-09T12:05:00Z", label: "", value: 18 },
        ],
        last: [{ time: "2026-06-09T12:05:00Z", label: "", value: 18 }],
      });
    }),
  );
  renderWithProviders(
    withTenant(
      <Routes>
        <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
      </Routes>,
    ),
    { route: "/devices/d1" },
  );
  // chart section titles appear
  expect(await screen.findByText(/CPU/i)).toBeInTheDocument();
  expect(await screen.findByText(/Memory/i)).toBeInTheDocument();
  // range selector present
  expect(screen.getByRole("button", { name: "24h" })).toBeInTheDocument();
});
```
**Important:** since `onUnhandledRequest:"error"`, the single `/metrics` handler with query param inspection covers ALL metrics requested by the section.

- [ ] **Step 2: Run and verify the failure**

Run: `npm test -- devicedetail` → FAIL (health section titles absent).

- [ ] **Step 3: Implement `DeviceHealthSection`**

Create `src/monitoring/DeviceHealthSection.tsx`:
```tsx
import { useState } from "react";
import { Group, SegmentedControl, SimpleGrid, Stack, Title } from "@mantine/core";
import { MetricChart } from "./MetricChart";
import { useDeviceMetrics } from "./hooks";
import type { MetricPoint, Range } from "./types";

function ChartFor({ deviceId, metric, title, unit, range }: {
  deviceId: string; metric: string; title: string; unit?: string; range: Range;
}) {
  const q = useDeviceMetrics(deviceId, metric, range);
  const points = (q.data?.points ?? []) as MetricPoint[];
  return <MetricChart title={title} points={points} unit={unit} />;
}

export function DeviceHealthSection({ deviceId }: { deviceId: string }) {
  const [range, setRange] = useState<Range>("24h");
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={4}>Health</Title>
        <SegmentedControl
          value={range}
          onChange={(v) => setRange(v as Range)}
          data={[
            { label: "1h", value: "1h" },
            { label: "24h", value: "24h" },
            { label: "7d", value: "7d" },
          ]}
        />
      </Group>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <ChartFor deviceId={deviceId} metric="cpu.pct" title="CPU" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="mem.pct" title="Memory" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="disk.pct" title="Disk" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="iface.bytes_in" title="Inbound traffic" unit="bytes" range={range} />
        <ChartFor deviceId={deviceId} metric="iface.bytes_out" title="Outbound traffic" unit="bytes" range={range} />
        <ChartFor deviceId={deviceId} metric="gateway.rtt_ms" title="Gateway RTT" unit="ms" range={range} />
        <ChartFor deviceId={deviceId} metric="gateway.loss_pct" title="Gateway loss" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="vpn.up" title="VPN up" range={range} />
      </SimpleGrid>
    </Stack>
  );
}
```
**Note:** Mantine's `SegmentedControl` renders segments as radio inputs with a label; the test looks for `getByRole("button", { name: "24h" })` — if in jsdom the role is `radio` instead of `button`, adapt the test assertion to `getByText("24h")` or `getByRole("radio", { name: "24h" })`. Verify and align the test to the actual markup.

- [ ] **Step 4: Hook the section into `DeviceDetailPage`**

In `src/pages/DeviceDetailPage.tsx`, import `DeviceHealthSection` and add it after the status cards (inside the `Stack`, before or after `DeviceActions`):
```tsx
import { DeviceHealthSection } from "../monitoring/DeviceHealthSection";
// ...
{deviceId && <DeviceHealthSection deviceId={deviceId} />}
```

- [ ] **Step 5: Run and verify the pass**

Run: `npm test -- devicedetail` → PASS (including existing tests). Align the range selector assertion to the actual role if needed (see Step 3 note).

- [ ] **Step 6: Typecheck + suite**

Run: `npm run build` → ok. Run: `npm test` → green.

- [ ] **Step 7: Commit**
```bash
git add src/pages/DeviceDetailPage.tsx src/monitoring/DeviceHealthSection.tsx src/pages/__tests__/devicedetail.test.tsx
git commit -m "feat(fe): DeviceDetail with health section (system+network charts + range selector)"
```

---

## Task 5: `AlertsPage` — table + active/historical filter

**Files:**
- Create: `src/pages/AlertsPage.tsx`, `src/pages/__tests__/alerts.test.tsx`
- Modify: `src/components/AppShell.tsx` (add `/alerts` route)

- [ ] **Step 1: `AlertsPage` test (failing)**

Create `src/pages/__tests__/alerts.test.tsx`:
```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { AlertsPage } from "../AlertsPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
        activeId: "t1", setActiveId: () => {}, loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const active = {
  id: "a1", device_id: "d1", type: "device.down", label: "", severity: "critical",
  opened_at: "2026-06-09T10:00:00Z", resolved_at: null, details: {},
};
const resolved = {
  id: "a2", device_id: "d1", type: "gateway.down", label: "wan", severity: "warning",
  opened_at: "2026-06-08T10:00:00Z", resolved_at: "2026-06-08T11:00:00Z", details: {},
};

describe("AlertsPage", () => {
  it("filters active vs history", async () => {
    server.use(
      http.get("/api/tenants/t1/alerts", ({ request }) => {
        const url = new URL(request.url);
        const a = url.searchParams.get("active");
        return HttpResponse.json(a === "false" ? [active, resolved] : [active]);
      }),
    );
    renderWithProviders(withTenant(<AlertsPage />));
    // default: active only
    expect(await screen.findByText("device.down")).toBeInTheDocument();
    expect(screen.queryByText("gateway.down")).not.toBeInTheDocument();
    // switch to history
    await userEvent.click(screen.getByRole("button", { name: /history/i }));
    await waitFor(() => expect(screen.getByText("gateway.down")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run and verify the failure**

Run: `npm test -- alerts` → FAIL.

- [ ] **Step 3: Implement `AlertsPage`**

Create `src/pages/AlertsPage.tsx`:
```tsx
import { useState } from "react";
import { Badge, Group, Loader, SegmentedControl, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useAlerts } from "../monitoring/hooks";

export function AlertsPage() {
  const [mode, setMode] = useState<"active" | "history">("active");
  const q = useAlerts(mode === "active");
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Alerts</Title>
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as "active" | "history")}
          data={[
            { label: "Active", value: "active" },
            { label: "History", value: "history" },
          ]}
        />
      </Group>
      {q.isLoading && <Loader />}
      {q.data && q.data.length === 0 && <Text c="dimmed">No alerts</Text>}
      {q.data && q.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Type</Table.Th>
              <Table.Th>Label</Table.Th>
              <Table.Th>Severity</Table.Th>
              <Table.Th>Opened</Table.Th>
              <Table.Th>Resolved</Table.Th>
              <Table.Th>Device</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>{a.type}</Table.Td>
                <Table.Td>{a.label || "—"}</Table.Td>
                <Table.Td>
                  <Badge color={a.severity === "critical" ? "red" : "yellow"}>{a.severity}</Badge>
                </Table.Td>
                <Table.Td>{new Date(a.opened_at).toLocaleString()}</Table.Td>
                <Table.Td>{a.resolved_at ? new Date(a.resolved_at).toLocaleString() : "—"}</Table.Td>
                <Table.Td><Link to={`/devices/${a.device_id}`}>{a.device_id.slice(0, 8)}</Link></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
```
**Note:** the test clicks a `button` named "History". `SegmentedControl` renders controls as `radio`/`label`. If the role is not `button`, adapt the test assertion to the actual markup (`getByText(/history/i)` or `getByRole("radio", { name: /history/i })`). Align test and markup.

- [ ] **Step 4: Add the `/alerts` route in `AppShell`**

In `src/components/AppShell.tsx`, import `AlertsPage` and add the route:
```tsx
import { AlertsPage } from "../pages/AlertsPage";
// inside <Routes>:
<Route path="/alerts" element={<AlertsPage />} />
```
(The "Alerts" navbar entry was already added in Task 3.)

- [ ] **Step 5: Run and verify the pass**

Run: `npm test -- alerts` → PASS.

- [ ] **Step 6: Typecheck + full suite + lint**

Run: `npm run build` → ok. Run: `npm test` → all green. Run: `npm run lint` → clean (fix any warnings introduced).

- [ ] **Step 7: Commit**
```bash
git add src/pages/AlertsPage.tsx src/pages/__tests__/alerts.test.tsx src/components/AppShell.tsx
git commit -m "feat(fe): AlertsPage with active/historical filter + /alerts route"
```

---

## Task 6: Technical debt

- [ ] **Step 1: Record 2D debt**

Append to this plan:

```markdown
## Technical debt (2D)

- **No auto-refresh of charts/alerts**: data is fetched on-load/range-change. Add
  `refetchInterval` (e.g. 60s) for live updates later.
- **Fixed ranges (1h/24h/7d)**: no custom date-picker. Add arbitrary ranges if needed.
- **Raw unit formatting**: interface traffic is in absolute bytes (counters), not
  rate (bytes/s); consider derivative/MB normalisation and formatted tooltips in the UI.
- **Limited chart assertions**: tests verify transform + title/empty-state presence, not
  SVG paths (jsdom/Recharts limitation). A Playwright e2e test would cover real rendering.
- **`gateway.up`/`vpn.up`/`iface.up` as 0/1 series**: charted as lines; consider status
  badges/heatmap instead of a line chart for boolean metrics.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase2-milestone2D-dashboard.md
git commit -m "docs: technical debt milestone 2D"
```

---

## Definition of "done" (2D)

- Navbar Overview / Devices / Alerts; reorganised routing (`/`=Overview) without breaking existing links.
- Overview shows fleet health summary + active customer alerts.
- DeviceDetail shows status + charts (CPU/mem/disk, interface traffic, gateways, VPN) with time-range selector.
- AlertsPage lists active and historical alerts with filter.
- All tenant-scoped (tenant change refetches), with loading/error/empty-state.
- Vitest suite green; `npm run build` (tsc) and `npm run lint` clean.
- **Phase 2 is complete**: poller → storage → API → dashboard.

---

## Technical debt (2D) — consolidated from reviews

- **No auto-refresh of charts/alerts**: data is fetched on-load/range-change. Add
  `refetchInterval` (e.g. 60s) for live updates later.
- **Missing per-chart loading/error states** (Task 4 review): during fetch each `MetricChart`
  shows the empty-state "No data yet" (does not distinguish loading from empty, nor handles
  individual metric errors). Add per-chart skeleton/error when refining the UX.
- **Fixed ranges (1h/24h/7d)**: no custom date-picker. Add arbitrary ranges if needed.
- **Raw unit formatting**: interface traffic is in absolute bytes (counters), not
  rate (bytes/s); consider derivative/MB normalisation and formatted tooltips in the UI.
- **Duplicate alerts table** (Task 5 review): `OverviewPage` and `AlertsPage` repeat the alerts
  table (differing only in the "Resolved" column). Extractable into a shared component.
- **Limited chart assertions**: tests verify transform + title/empty-state presence, not
  SVG paths (jsdom/Recharts limitation). A Playwright e2e test would cover real rendering.
- **`gateway.up`/`vpn.up`/`iface.up` as 0/1 series**: charted as lines; consider status
  badges/heatmap instead of a line chart for boolean metrics.
