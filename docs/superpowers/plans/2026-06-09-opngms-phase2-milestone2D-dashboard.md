# OPNGMS — Fase 2 / Milestone 2D: Dashboard Frontend — Piano di Implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Una dashboard di monitoraggio (React/Mantine) che consuma gli endpoint 2C (`/metrics`, `/health`, `/alerts`): overview di flotta per-cliente, salute per-device con grafici, e una pagina alert con filtro attivi/storico.

**Architecture:** Estende il frontend di Milestone D (Vite + React 19 + Mantine v9 + React Router + TanStack Query + client `openapi-fetch` tipizzato). Aggiunge un feature-folder `src/monitoring/` (hook per-endpoint, util time-range, componenti grafico/card) e tre pagine (Overview nuova come landing, DeviceDetail estesa, Alerts nuova), con il routing riorganizzato. Grafici via `@mantine/charts` (su Recharts). Test Vitest + RTL + MSW.

**Tech Stack:** React 19, Mantine v9 (`@mantine/core` + nuovo `@mantine/charts`), `recharts`, TanStack Query v5, React Router v7, `openapi-fetch`, Vitest + Testing Library + MSW.

---

## Contesto per l'implementatore (leggere prima di iniziare)

Codebase frontend esistente in `/home/l0rdg3x/coding/OPNGMS/frontend`. **Segui i pattern esistenti.**

- **Client API tipizzato** (`src/api/client.ts`): singleton `api` (`openapi-fetch`), già con CSRF middleware e `credentials:include`. Uso: `api.GET("/api/tenants/{tenant_id}/...", { params: { path: {...}, query: {...} } })` → ritorna `{ data, error }`. I tipi vengono da `src/api/schema.d.ts` (**da rigenerare**, Task 1).
- **Tenant context** (`src/tenant/TenantProvider.tsx`, `useTenant.ts`): `useTenant()` → `{ tenants, activeId, setActiveId, loading }`. `activeId` è il tenant corrente (string|null). Gli hook dati devono essere `enabled: !!activeId`.
- **Pattern query** (vedi `src/pages/DeviceDetailPage.tsx`): `useQuery({ queryKey: ["device", activeId, deviceId], enabled: !!activeId && !!deviceId, queryFn: async () => { const {data} = await api.GET(...); return data; } })`.
- **AppShell** (`src/components/AppShell.tsx`): header (TenantSwitcher + logout) + navbar (oggi un solo `NavLink` "Device" → `/`) + `<Routes>` dentro `MantineAppShell.Main`. Oggi: `/`=`DevicesPage`, `/devices/:deviceId`=`DeviceDetailPage`.
- **Test** (`src/test/utils.tsx`): `renderWithProviders(ui, { route })` avvolge in `MantineProvider` + `QueryClientProvider` (retry:false) + `MemoryRouter`. Il tenant si inietta avvolgendo in `<TenantContext.Provider value={{tenants, activeId, setActiveId, loading}}>` (vedi `src/pages/__tests__/devicedetail.test.tsx`, helper `withTenant`). MSW: `server.use(http.get("/api/tenants/t1/...", () => HttpResponse.json(...)))`. **`onUnhandledRequest: "error"`** (`src/test/setup.ts`): ogni endpoint chiamato da una pagina DEVE essere mockato, altrimenti il test fallisce.
- **CSS Mantine** importati in `src/main.tsx`: va aggiunto `import "@mantine/charts/styles.css"`.

**Comandi** (dalla dir `frontend/`):
- Test: `npm test` (vitest run). Oggi i test esistenti sono verdi.
- Lint/typecheck: `npm run lint` ed `npm run build` (`tsc -b && vite build`).
- Rigenerazione tipi API: `npm run gen:api` — **richiede le env del backend** (importa `app.main`): eseguire come
  `SESSION_SECRET=x MASTER_KEY="$(cd ../backend && .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms npm run gen:api`.

**Nomi metrica** (confermati in `backend/app/services/monitoring.py`): `cpu.pct`, `mem.pct`, `disk.pct`, `uptime.seconds`, `iface.bytes_in`, `iface.bytes_out`, `iface.up`, `gateway.rtt_ms`, `gateway.loss_pct`, `gateway.up`, `vpn.up`.

**Strategia test grafici (importante):** Mantine Charts usa Recharts `ResponsiveContainer`, che in jsdom non ha dimensioni → non renderizza i path SVG in modo affidabile. Quindi: (1) la logica di trasformazione dati è una **funzione pura testata a parte**; (2) i test dei componenti grafico/pagina asseriscono **testo/strutture** (titoli, valori da `/health` e `/alerts`, empty-state), **non** i path SVG; (3) Task 1 aggiunge un mock di `ResizeObserver` in `setup.ts` come rete di sicurezza.

---

## File Structure

| File | Responsabilità | Azione |
|------|----------------|--------|
| `package.json` / lockfile | Aggiunge `@mantine/charts` + `recharts` | Modify (via npm i) |
| `src/api/schema.d.ts` | Tipi rigenerati (include endpoint 2C) | Regen |
| `src/main.tsx` | Import `@mantine/charts/styles.css` | Modify |
| `src/test/setup.ts` | Mock `ResizeObserver` | Modify |
| `src/monitoring/range.ts` | `rangeToParams(range, now)` (util pura) | Create |
| `src/monitoring/types.ts` | Tipi locali (`MetricPoint`, `Range`) derivati dallo schema | Create |
| `src/monitoring/hooks.ts` | `useTenantHealth`, `useAlerts`, `useDeviceMetrics` | Create |
| `src/monitoring/MetricChart.tsx` | Wrapper grafico + `toChartData` (puro) | Create |
| `src/monitoring/HealthSummaryCards.tsx` | Card riepilogo da `/health` | Create |
| `src/pages/OverviewPage.tsx` | Landing: health + alert attivi | Create |
| `src/pages/AlertsPage.tsx` | Tabella alert + filtro attivi/storico | Create |
| `src/pages/DeviceDetailPage.tsx` | + sezione salute (grafici + time-range) | Modify |
| `src/components/AppShell.tsx` | Routing + navbar (Overview/Device/Alert) | Modify |
| `src/monitoring/__tests__/*.test.tsx(ts)` | Test unità/componenti | Create |
| `src/pages/__tests__/*.test.tsx` | Test pagine | Create |

---

## Task 1: Data layer (install grafici, schema, hook, util time-range, infra test)

**Files:**
- Modify: `package.json` (npm i), `src/main.tsx`, `src/test/setup.ts`
- Regen: `src/api/schema.d.ts`
- Create: `src/monitoring/range.ts`, `src/monitoring/types.ts`, `src/monitoring/hooks.ts`
- Create: `src/monitoring/__tests__/range.test.ts`

- [ ] **Step 1: Installare la libreria grafici**

Dalla dir `frontend/`:
```bash
npm i @mantine/charts recharts
```
Verifica che `@mantine/charts` e `recharts` compaiano in `package.json` → `dependencies`.

- [ ] **Step 2: Rigenerare i tipi API (include gli endpoint 2C)**

```bash
SESSION_SECRET=x \
MASTER_KEY="$(cd ../backend && .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
npm run gen:api
```
Verifica che `src/api/schema.d.ts` ora contenga i path `/api/tenants/{tenant_id}/devices/{device_id}/metrics`, `/api/tenants/{tenant_id}/health`, `/api/tenants/{tenant_id}/alerts` (es. `grep -c "/health" src/api/schema.d.ts`). Se il comando fallisce per env mancanti, aggiungi le env mancanti come per i test backend.

- [ ] **Step 3: Importare il CSS dei grafici**

In `src/main.tsx`, dopo `import "@mantine/notifications/styles.css";` aggiungi:
```ts
import "@mantine/charts/styles.css";
```

- [ ] **Step 4: Aggiungere il mock di ResizeObserver ai test**

In `src/test/setup.ts`, dopo il blocco `matchMedia`, aggiungi:
```ts
// Recharts ResponsiveContainer (usato da @mantine/charts) osserva le dimensioni via
// ResizeObserver, assente in jsdom. Mock no-op: i test dei grafici asseriscono dati/strutture,
// non dimensioni.
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock as unknown as typeof ResizeObserver;
```

- [ ] **Step 5: Util time-range — scrivere il test (fallisce)**

Crea `src/monitoring/__tests__/range.test.ts`:
```ts
import { describe, expect, it } from "vitest";
import { rangeToParams } from "../range";

describe("rangeToParams", () => {
  const now = new Date("2026-06-09T12:00:00.000Z");

  it("1h → finestra 1h, bucket 60s", () => {
    const p = rangeToParams("1h", now);
    expect(p.to).toBe("2026-06-09T12:00:00.000Z");
    expect(p.from).toBe("2026-06-09T11:00:00.000Z");
    expect(p.bucket).toBe(60);
  });

  it("24h → finestra 24h, bucket 300s", () => {
    const p = rangeToParams("24h", now);
    expect(p.from).toBe("2026-06-08T12:00:00.000Z");
    expect(p.bucket).toBe(300);
  });

  it("7d → finestra 7g, bucket 3600s", () => {
    const p = rangeToParams("7d", now);
    expect(p.from).toBe("2026-06-02T12:00:00.000Z");
    expect(p.bucket).toBe(3600);
  });
});
```

- [ ] **Step 6: Eseguire il test e verificarne il fallimento**

Run: `npm test -- range`
Expected: FAIL (modulo `../range` inesistente).

- [ ] **Step 7: Implementare util + tipi**

Crea `src/monitoring/types.ts`:
```ts
export type Range = "1h" | "24h" | "7d";

// Forma di un punto serie come restituito da GET .../metrics (vedi MetricPoint backend).
export interface MetricPoint {
  time: string;
  label: string;
  value: number;
}
```

Crea `src/monitoring/range.ts`:
```ts
import type { Range } from "./types";

const SPAN_SECONDS: Record<Range, number> = { "1h": 3600, "24h": 86400, "7d": 604800 };
const BUCKET_SECONDS: Record<Range, number> = { "1h": 60, "24h": 300, "7d": 3600 };

export interface RangeParams {
  from: string;
  to: string;
  bucket: number;
}

/** Converte un preset di range nei query param dell'endpoint metriche.
 *  bucket scelto per restare sotto MAX_POINTS (5000) lato API e dare grafici lisci. */
export function rangeToParams(range: Range, now: Date): RangeParams {
  const to = now;
  const from = new Date(now.getTime() - SPAN_SECONDS[range] * 1000);
  return { from: from.toISOString(), to: to.toISOString(), bucket: BUCKET_SECONDS[range] };
}
```

- [ ] **Step 8: Eseguire il test e verificarne il passaggio**

Run: `npm test -- range`
Expected: PASS (3/3).

- [ ] **Step 9: Implementare gli hook dati**

Crea `src/monitoring/hooks.ts`:
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
**Nota:** i nomi esatti dei query param (`from`/`to`/`bucket`/`active`) e i tipi vengono da `schema.d.ts` rigenerato. Se TypeScript segnala un mismatch sul nome di un parametro, allinea al nome reale nello schema (l'endpoint backend usa alias `from`/`bucket`). Esegui `npm run build` per il typecheck.

- [ ] **Step 10: Typecheck + suite**

Run: `npm run build` (tsc) — nessun errore di tipo sugli hook.
Run: `npm test` — tutti i test verdi (esistenti + `range`).

- [ ] **Step 11: Commit**
```bash
git add package.json package-lock.json src/main.tsx src/test/setup.ts src/api/schema.d.ts src/monitoring/
git commit -m "feat(fe): data layer 2D (charts install, schema 2C, hook metriche/health/alert, range util)"
```

---

## Task 2: Componenti base — `MetricChart` + `HealthSummaryCards`

**Files:**
- Create: `src/monitoring/MetricChart.tsx`, `src/monitoring/HealthSummaryCards.tsx`
- Create: `src/monitoring/__tests__/metricchart.test.tsx`, `src/monitoring/__tests__/healthcards.test.tsx`

- [ ] **Step 1: Test di `toChartData` + smoke di `MetricChart` (fallisce)**

Crea `src/monitoring/__tests__/metricchart.test.tsx`:
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
  it("raggruppa per timestamp con una serie per label", () => {
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

  it("label vuota → serie 'value'", () => {
    const { series } = toChartData(points);
    expect(series).toEqual(["value"]);
  });
});

describe("MetricChart", () => {
  it("mostra il titolo e non crasha con dati", () => {
    render(
      <MantineProvider>
        <MetricChart title="CPU %" points={points} />
      </MantineProvider>,
    );
    expect(screen.getByText("CPU %")).toBeInTheDocument();
  });

  it("mostra empty-state senza dati", () => {
    render(
      <MantineProvider>
        <MetricChart title="CPU %" points={[]} />
      </MantineProvider>,
    );
    expect(screen.getByText(/nessun dato/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `npm test -- metricchart`
Expected: FAIL (modulo inesistente).

- [ ] **Step 3: Implementare `MetricChart`**

Crea `src/monitoring/MetricChart.tsx`:
```tsx
import { Card, Text } from "@mantine/core";
import { LineChart } from "@mantine/charts";
import type { MetricPoint } from "./types";

export interface ChartData {
  data: Record<string, number | string>[];
  series: string[];
}

/** Trasforma punti {time,label,value} in righe per timestamp con una colonna per label.
 *  Label vuota ('') → colonna 'value'. Funzione pura, testata a parte. */
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
          Nessun dato ancora
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

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `npm test -- metricchart`
Expected: PASS. (Se `LineChart` lancia in jsdom nonostante il mock di ResizeObserver, avvolgi il render del grafico in modo che l'empty-path resti testabile; ma con il mock di Step 1.4 il render non deve crashare — asseriamo solo il titolo, non i path SVG.)

- [ ] **Step 5: Test `HealthSummaryCards` (fallisce)**

Crea `src/monitoring/__tests__/healthcards.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { HealthSummaryCards } from "../HealthSummaryCards";

describe("HealthSummaryCards", () => {
  it("mostra totale device, conteggi per stato e alert attivi", () => {
    render(
      <MantineProvider>
        <HealthSummaryCards
          health={{ total_devices: 3, by_status: { reachable: 2, unverified: 1 }, active_alerts: 4 }}
        />
      </MantineProvider>,
    );
    expect(screen.getByText("3")).toBeInTheDocument(); // totale
    expect(screen.getByText(/reachable/i)).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument(); // alert attivi
  });
});
```

- [ ] **Step 6: Eseguire e verificare il fallimento**

Run: `npm test -- healthcards` → FAIL.

- [ ] **Step 7: Implementare `HealthSummaryCards`**

Crea `src/monitoring/HealthSummaryCards.tsx`:
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
        <Text size="sm" c="dimmed">Device totali</Text>
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
        <Text size="sm" c="dimmed">Alert attivi</Text>
        <Title order={2}>{health.active_alerts}</Title>
      </Card>
    </SimpleGrid>
  );
}
```

- [ ] **Step 8: Eseguire e verificare il passaggio**

Run: `npm test -- healthcards` → PASS. Poi `npm test` intero → verde.

- [ ] **Step 9: Commit**
```bash
git add src/monitoring/MetricChart.tsx src/monitoring/HealthSummaryCards.tsx src/monitoring/__tests__/
git commit -m "feat(fe): componenti MetricChart (+ toChartData) e HealthSummaryCards"
```

---

## Task 3: `OverviewPage` + riorganizzazione routing/navbar

**Files:**
- Create: `src/pages/OverviewPage.tsx`, `src/pages/__tests__/overview.test.tsx`
- Modify: `src/components/AppShell.tsx`

- [ ] **Step 1: Test `OverviewPage` (fallisce)**

Crea `src/pages/__tests__/overview.test.tsx`:
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
  it("mostra health e alert attivi", async () => {
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

  it("empty-state senza alert attivi", async () => {
    server.use(
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<OverviewPage />));
    expect(await screen.findByText(/nessun alert attivo/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `npm test -- overview` → FAIL.

- [ ] **Step 3: Implementare `OverviewPage`**

Crea `src/pages/OverviewPage.tsx`:
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
      {health.error && <Alert color="red">Errore nel caricamento della salute flotta</Alert>}
      {health.data && <HealthSummaryCards health={health.data as FleetHealth} />}

      <Title order={4} mt="md">Alert attivi</Title>
      {alerts.isLoading && <Loader />}
      {alerts.data && alerts.data.length === 0 && (
        <Text c="dimmed">Nessun alert attivo</Text>
      )}
      {alerts.data && alerts.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Tipo</Table.Th>
              <Table.Th>Etichetta</Table.Th>
              <Table.Th>Severità</Table.Th>
              <Table.Th>Aperto</Table.Th>
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
**Nota tipi:** `alerts.data`/`health.data` sono tipizzati dallo schema generato. Se i tipi generati differiscono (es. `details` opzionale), adatta gli accessi; non usare `any` se evitabile (i cast `as FleetHealth` sono ammessi dove lo schema è più largo).

- [ ] **Step 4: Eseguire e verificare il passaggio**

Run: `npm test -- overview` → PASS (2/2).

- [ ] **Step 5: Riorganizzare routing + navbar in `AppShell`**

In `src/components/AppShell.tsx`:
- import `OverviewPage` e `AlertsPage` (quest'ultima creata nel Task 5; per ora, se AlertsPage non esiste ancora, NON importarla — aggiungi la sua rotta nel Task 5. In questo task aggiungi solo Overview + sposta Devices).
- import aggiuntivi: `OverviewPage` da `../pages/OverviewPage`.

Sostituisci il blocco navbar:
```tsx
<MantineAppShell.Navbar p="sm">
  <NavLink component={RouterNavLink} to="/" label="Overview" />
  <NavLink component={RouterNavLink} to="/devices" label="Device" />
  <NavLink component={RouterNavLink} to="/alerts" label="Alert" />
</MantineAppShell.Navbar>
```
e il blocco Routes:
```tsx
<Routes>
  <Route path="/" element={<OverviewPage />} />
  <Route path="/devices" element={<DevicesPage />} />
  <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
</Routes>
```
(La rotta `/alerts` viene aggiunta nel Task 5.)

**Aggiorna i link interni** che puntavano a `/` per la lista device: cerca `to="/"`/`navigate("/")` nei componenti/pagine (es. dopo creazione/eliminazione device) e reindirizza a `/devices` dove il senso era "torna alla lista device". Esegui:
```bash
grep -rn '"/"' src --include=*.tsx | grep -v OverviewPage
```
e correggi i casi che intendevano la lista device (es. in `DeviceActions`/`DevicesPage`/test di delete che si aspettano "home"). Aggiorna i test impattati di conseguenza (es. `devicedetail.test.tsx` delete → la rotta di ritorno).

- [ ] **Step 6: Typecheck + suite intera**

Run: `npm run build` → nessun errore.
Run: `npm test` → tutti verdi (aggiorna eventuali test che dipendevano da `/`=Devices).

- [ ] **Step 7: Commit**
```bash
git add src/pages/OverviewPage.tsx src/pages/__tests__/overview.test.tsx src/components/AppShell.tsx
git commit -m "feat(fe): OverviewPage (health + alert attivi) + routing/navbar riorganizzati"
```

---

## Task 4: `DeviceDetailPage` esteso — sezione salute con grafici

**Files:**
- Modify: `src/pages/DeviceDetailPage.tsx`
- Create: `src/monitoring/DeviceHealthSection.tsx`
- Modify/Create: `src/pages/__tests__/devicedetail.test.tsx` (aggiungere test salute)

- [ ] **Step 1: Test della sezione salute (fallisce)**

Aggiungi a `src/pages/__tests__/devicedetail.test.tsx` un nuovo test (mantieni quelli esistenti). Mocka l'endpoint metriche con un handler che ispeziona il query param `metric` e ritorna una serie; mocka anche il GET device:
```tsx
it("mostra la sezione salute con grafici e selettore range", async () => {
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
  // i titoli dei grafici della sezione salute compaiono
  expect(await screen.findByText(/CPU/i)).toBeInTheDocument();
  expect(await screen.findByText(/Memoria/i)).toBeInTheDocument();
  // selettore range presente
  expect(screen.getByRole("button", { name: "24h" })).toBeInTheDocument();
});
```
**Importante:** poiché `onUnhandledRequest:"error"`, l'handler unico su `/metrics` con ispezione del query param copre TUTTE le metriche richieste dalla sezione.

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `npm test -- devicedetail` → FAIL (titoli salute assenti).

- [ ] **Step 3: Implementare `DeviceHealthSection`**

Crea `src/monitoring/DeviceHealthSection.tsx`:
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
        <Title order={4}>Salute</Title>
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
        <ChartFor deviceId={deviceId} metric="mem.pct" title="Memoria" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="disk.pct" title="Disco" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="iface.bytes_in" title="Traffico in" unit="bytes" range={range} />
        <ChartFor deviceId={deviceId} metric="iface.bytes_out" title="Traffico out" unit="bytes" range={range} />
        <ChartFor deviceId={deviceId} metric="gateway.rtt_ms" title="Gateway RTT" unit="ms" range={range} />
        <ChartFor deviceId={deviceId} metric="gateway.loss_pct" title="Gateway loss" unit="%" range={range} />
        <ChartFor deviceId={deviceId} metric="vpn.up" title="VPN up" range={range} />
      </SimpleGrid>
    </Stack>
  );
}
```
**Nota:** `SegmentedControl` di Mantine rende i segmenti come radio con label; il test cerca `getByRole("button", { name: "24h" })` — se in jsdom il ruolo è `radio` invece di `button`, adatta l'asserzione del test a `getByText("24h")` o `getByRole("radio", { name: "24h" })`. Verifica e allinea il test al markup reale.

- [ ] **Step 4: Agganciare la sezione in `DeviceDetailPage`**

In `src/pages/DeviceDetailPage.tsx`, importa `DeviceHealthSection` e aggiungila dopo le card di stato (dentro lo `Stack`, prima o dopo `DeviceActions`):
```tsx
import { DeviceHealthSection } from "../monitoring/DeviceHealthSection";
// ...
{deviceId && <DeviceHealthSection deviceId={deviceId} />}
```

- [ ] **Step 5: Eseguire e verificare il passaggio**

Run: `npm test -- devicedetail` → PASS (inclusi i test esistenti). Allinea l'asserzione del selettore al ruolo reale se necessario (vedi nota Step 3).

- [ ] **Step 6: Typecheck + suite**

Run: `npm run build` → ok. Run: `npm test` → verde.

- [ ] **Step 7: Commit**
```bash
git add src/pages/DeviceDetailPage.tsx src/monitoring/DeviceHealthSection.tsx src/pages/__tests__/devicedetail.test.tsx
git commit -m "feat(fe): DeviceDetail con sezione salute (grafici essenziale+rete + selettore range)"
```

---

## Task 5: `AlertsPage` — tabella + filtro attivi/storico

**Files:**
- Create: `src/pages/AlertsPage.tsx`, `src/pages/__tests__/alerts.test.tsx`
- Modify: `src/components/AppShell.tsx` (aggiungi rotta `/alerts`)

- [ ] **Step 1: Test `AlertsPage` (fallisce)**

Crea `src/pages/__tests__/alerts.test.tsx`:
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
  it("filtra attivi vs storico", async () => {
    server.use(
      http.get("/api/tenants/t1/alerts", ({ request }) => {
        const url = new URL(request.url);
        const a = url.searchParams.get("active");
        return HttpResponse.json(a === "false" ? [active, resolved] : [active]);
      }),
    );
    renderWithProviders(withTenant(<AlertsPage />));
    // default: solo attivi
    expect(await screen.findByText("device.down")).toBeInTheDocument();
    expect(screen.queryByText("gateway.down")).not.toBeInTheDocument();
    // passa a storico
    await userEvent.click(screen.getByRole("button", { name: /storico/i }));
    await waitFor(() => expect(screen.getByText("gateway.down")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Eseguire e verificare il fallimento**

Run: `npm test -- alerts` → FAIL.

- [ ] **Step 3: Implementare `AlertsPage`**

Crea `src/pages/AlertsPage.tsx`:
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
        <Title order={3}>Alert</Title>
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as "active" | "history")}
          data={[
            { label: "Attivi", value: "active" },
            { label: "Storico", value: "history" },
          ]}
        />
      </Group>
      {q.isLoading && <Loader />}
      {q.data && q.data.length === 0 && <Text c="dimmed">Nessun alert</Text>}
      {q.data && q.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Tipo</Table.Th>
              <Table.Th>Etichetta</Table.Th>
              <Table.Th>Severità</Table.Th>
              <Table.Th>Aperto</Table.Th>
              <Table.Th>Risolto</Table.Th>
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
**Nota:** il test clicca un `button` con nome "storico". `SegmentedControl` rende i controlli come `radio`/`label`. Se il ruolo non è `button`, adatta l'asserzione del test al markup reale (`getByText(/storico/i)` o `getByRole("radio", { name: /storico/i })`). Allinea test e markup.

- [ ] **Step 4: Aggiungere la rotta `/alerts` in `AppShell`**

In `src/components/AppShell.tsx`, importa `AlertsPage` e aggiungi la rotta:
```tsx
import { AlertsPage } from "../pages/AlertsPage";
// dentro <Routes>:
<Route path="/alerts" element={<AlertsPage />} />
```
(La voce navbar "Alert" è già stata aggiunta nel Task 3.)

- [ ] **Step 5: Eseguire e verificare il passaggio**

Run: `npm test -- alerts` → PASS.

- [ ] **Step 6: Typecheck + suite intera + lint**

Run: `npm run build` → ok. Run: `npm test` → tutti verdi. Run: `npm run lint` → pulito (correggi eventuali warning introdotti).

- [ ] **Step 7: Commit**
```bash
git add src/pages/AlertsPage.tsx src/pages/__tests__/alerts.test.tsx src/components/AppShell.tsx
git commit -m "feat(fe): AlertsPage con filtro attivi/storico + rotta /alerts"
```

---

## Task 6: Debito tecnico

- [ ] **Step 1: Registrare il debito 2D**

Append a questo piano:
```markdown
## Debito tecnico (2D)

- **Nessun auto-refresh dei grafici/alert**: i dati si fetchano on-load/cambio-range. Aggiungere
  `refetchInterval` (es. 60s) per un aggiornamento live in un secondo momento.
- **Range fissi (1h/24h/7d)**: niente date-picker custom. Aggiungere range arbitrari se richiesto.
- **Formattazione unità grezza**: i traffici interfaccia sono in bytes assoluti (contatori), non
  rate (bytes/s); valutare derivata/normalizzazione MB e tooltip formattati nella UI.
- **Asserzioni grafici limitate**: i test verificano transform + presenza titoli/empty-state, non i
  path SVG (limite jsdom/Recharts). Un test e2e (Playwright) coprirebbe il rendering reale.
- **`gateway.up`/`vpn.up`/`iface.up` come serie 0/1**: graficati come linee; valutare badge/heatmap
  di stato invece di un line chart per le metriche booleane.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase2-milestone2D-dashboard.md
git commit -m "docs: debito tecnico milestone 2D"
```

---

## Definizione di "fatto" (2D)

- Navbar Overview / Device / Alert; routing riorganizzato (`/`=Overview) senza rompere i link esistenti.
- Overview mostra riepilogo salute flotta + alert attivi del cliente.
- DeviceDetail mostra stato + grafici (CPU/mem/disco, traffico interfacce, gateway, VPN) con selettore time-range.
- AlertsPage lista attivi e storico con filtro.
- Tutto tenant-scoped (cambio tenant rifetcha), con loading/error/empty-state.
- Suite Vitest verde; `npm run build` (tsc) e `npm run lint` puliti.
- **La Fase 2 è completa**: poller → storage → API → dashboard.
