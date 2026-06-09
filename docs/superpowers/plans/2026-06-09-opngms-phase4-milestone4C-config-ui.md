# OPNGMS — Phase 4 / Milestone 4C: Firewall-aware Config UI (read-only) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only "Config" tab in `DeviceDetailPage` that renders the device's config as a collapsible tree (sensitive values masked) plus a firewall-aware capabilities panel (interfaces, OPNsense version, configured sections, available capabilities), consuming the 4B `/config/model` + `/config/capabilities` APIs.

**Architecture:** Extends the 2D frontend (React 19 + Mantine v9 + TanStack Query + typed `openapi-fetch` + i18n layer). Reorganizes `DeviceDetailPage` into Mantine Tabs (Info | Health | Config); the Config tab uses two new tenant-scoped hooks and recursive tree + panel components. No mutations (read-only).

**Tech Stack:** React 19, Mantine v9, TanStack Query v5, React Router v7, `openapi-fetch`, Vitest + RTL + MSW.

---

## Context for the implementer (read first)

Codebase is **English** — write all code, comments, and UI strings (via i18n) in English. Phases 1–4B in `main`.

- **i18n**: `src/i18n/index.ts` (`useT()` returns the dict), `src/i18n/en.ts` (typed nested dict). Add a `config: {...}` group + `errors.configModelLoad`/`errors.configCapabilitiesLoad`. Components read strings via `const t = useT();` → `t.config.x`. Hooks (outside render) import `en` directly (see `src/monitoring/hooks.ts`).
- **Hooks pattern**: `src/monitoring/hooks.ts` — `useQuery`, tenant-scoped (`useTenant().activeId`, `enabled: !!activeId && !!deviceId`), `if (error) throw new Error(en.errors.x)`. For 4C, a **404 (no snapshot yet)** must NOT throw — destructure `response` and `if (response.status === 404) return null` before the error throw.
- **Typed client**: `src/api/client.ts` (`api.GET(path, { params: { path: {...} } })` → `{ data, error, response }`). `/config/capabilities` is typed (`CapabilityInventory`); `/config/model` is `dict` → cast to a local `ConfigNode` type.
- **DeviceDetailPage**: `src/pages/DeviceDetailPage.tsx` — currently device card + `DeviceHealthSection` + `DeviceActions` in a `Stack`, uses `useT()`. Reorganize into `Tabs`.
- **Tests**: `src/test/utils.tsx` (`renderWithProviders`, wraps Mantine + Query + Router + I18n), `src/pages/__tests__/devicedetail.test.tsx` (existing — `withTenant` helper, MSW `server.use`, device mock). MSW `onUnhandledRequest:"error"` → mock every endpoint a page calls. `src/monitoring/__tests__/*` for component test style.

**Commands** (from `frontend/`): `npm test`, `npm run build` (tsc), `npm run lint`. Schema regen: `npm run gen:api` (needs backend env — see the 2D plan; provide `SESSION_SECRET`/`MASTER_KEY`/`DATABASE_URL`/`ADMIN_DATABASE_URL`). Current frontend suite: **19 tests green** (10 files).

**Security:** the backend already redacts sensitive values (they arrive `value: null, sensitive: true`), so the UI literally cannot display a secret. The masking (`•••• 🔒`) is cosmetic; tests still assert no secret string appears in the DOM. Read-only — no mutations.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `src/api/schema.d.ts` | regen (4B endpoints) | Regen |
| `src/config/types.ts` | `ConfigNode` type | Create |
| `src/config/hooks.ts` | `useConfigModel`, `useConfigCapabilities` | Create |
| `src/config/CapabilitiesPanel.tsx` | capabilities card | Create |
| `src/config/ConfigTree.tsx` | recursive tree (collapsible, masked) | Create |
| `src/config/ConfigTab.tsx` | the Config tab (hooks + panel + tree + states) | Create |
| `src/i18n/en.ts` | `config.*` + `errors.config*` strings | Modify |
| `src/pages/DeviceDetailPage.tsx` | reorganize into Tabs | Modify |
| `src/config/__tests__/*`, `src/pages/__tests__/devicedetail.test.tsx` | tests | Create/Modify |

---

## Task 1: Data layer (schema regen + hooks + ConfigNode type)

**Files:**
- Regen: `src/api/schema.d.ts`; Create: `src/config/types.ts`, `src/config/hooks.ts`
- Modify: `src/i18n/en.ts` (add `errors.configModelLoad`/`errors.configCapabilitiesLoad`)

- [ ] **Step 1: Regenerate the API types**
```bash
SESSION_SECRET=x \
MASTER_KEY="$(cd ../backend && .venv/bin/python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms \
npm run gen:api
```
Confirm `src/api/schema.d.ts` now contains `/config/model` and `/config/capabilities` paths and the `CapabilityInventory` schema.

- [ ] **Step 2: Add the `ConfigNode` type**

Create `src/config/types.ts`:
```ts
// Mirrors the backend config_model node (GET /config/model is response_model=dict).
export interface ConfigNode {
  tag: string;
  path: string;
  attributes: Record<string, string | null>;
  children: ConfigNode[];
  value: string | null;
  sensitive: boolean;
}
```

- [ ] **Step 3: Add i18n error strings**

In `src/i18n/en.ts`, add to the `errors` group:
```ts
    configModelLoad: "Failed to load configuration",
    configCapabilitiesLoad: "Failed to load capabilities",
```

- [ ] **Step 4: Write the hooks**

Create `src/config/hooks.ts`:
```ts
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";
import type { ConfigNode } from "./types";

export function useConfigModel(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["config-model", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<ConfigNode | null> => {
      const { data, error, response } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/model",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (response.status === 404) return null; // no snapshot yet -> empty state
      if (error) throw new Error(en.errors.configModelLoad);
      return data as ConfigNode;
    },
  });
}

export function useConfigCapabilities(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["config-capabilities", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/capabilities",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (response.status === 404) return null;
      if (error) throw new Error(en.errors.configCapabilitiesLoad);
      return data ?? null;
    },
  });
}
```

- [ ] **Step 5: Typecheck**

Run: `npm run build` → no type errors (the hooks compile against the regenerated schema). `npm test` → existing suite still green.

- [ ] **Step 6: Commit**
```bash
git add src/api/schema.d.ts openapi.json src/config/types.ts src/config/hooks.ts src/i18n/en.ts
git commit -m "feat(fe): config data layer (schema 4B, useConfigModel/useConfigCapabilities, ConfigNode)"
```

---

## Task 2: `CapabilitiesPanel`

**Files:**
- Create: `src/config/CapabilitiesPanel.tsx`, `src/config/__tests__/capabilitiespanel.test.tsx`
- Modify: `src/i18n/en.ts` (add `config.*` strings)

- [ ] **Step 1: Add i18n strings**

In `src/i18n/en.ts`, add a `config` group:
```ts
  config: {
    tabInfo: "Info",
    tabHealth: "Health",
    tabConfig: "Config",
    capabilities: "Capabilities",
    version: "OPNsense version",
    interfaces: "Interfaces",
    configuredSections: "Configured sections",
    available: "Available capabilities",
    hidden: "hidden",
    noConfigYet: "No configuration captured yet",
    nic: "NIC",
  },
```

- [ ] **Step 2: Write the failing test**

Create `src/config/__tests__/capabilitiespanel.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import { CapabilitiesPanel } from "../CapabilitiesPanel";

const inv = {
  opnsense_version: "24.7.2",
  interfaces: [
    { name: "wan", nic: "igb0", description: "WAN" },
    { name: "lan", nic: "igb1", description: "LAN" },
  ],
  configured_sections: ["system", "interfaces", "filter"],
  available_capabilities: [
    { id: "os-wireguard", label: "WireGuard VPN", area: "vpn/wireguard" },
  ],
};

function wrap(ui: React.ReactNode) {
  return (
    <MantineProvider>
      <I18nProvider>{ui}</I18nProvider>
    </MantineProvider>
  );
}

describe("CapabilitiesPanel", () => {
  it("shows version, interfaces, sections and available capabilities", () => {
    render(wrap(<CapabilitiesPanel inv={inv} />));
    expect(screen.getByText("24.7.2")).toBeInTheDocument();
    expect(screen.getByText("igb0")).toBeInTheDocument();
    expect(screen.getByText(/WireGuard VPN/)).toBeInTheDocument();
    expect(screen.getByText("filter")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run and verify it fails** — `npm test -- capabilitiespanel` → FAIL.

- [ ] **Step 4: Implement `CapabilitiesPanel`**

Create `src/config/CapabilitiesPanel.tsx`. Type the prop from the generated schema (`components["schemas"]["CapabilityInventory"]`) or accept a structural type.
```tsx
import { Badge, Card, Group, Stack, Table, Text, Title } from "@mantine/core";
import { useT } from "../i18n";
import type { components } from "../api/schema";

type Inventory = components["schemas"]["CapabilityInventory"];

export function CapabilitiesPanel({ inv }: { inv: Inventory }) {
  const t = useT();
  return (
    <Card withBorder>
      <Title order={5} mb="xs">{t.config.capabilities}</Title>
      <Stack gap="xs">
        <Text size="sm">{t.config.version}: <b>{inv.opnsense_version || "—"}</b></Text>

        <Text size="sm" fw={600}>{t.config.interfaces}</Text>
        <Table withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.config.interfaces}</Table.Th>
              <Table.Th>{t.config.nic}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {inv.interfaces.map((i) => (
              <Table.Tr key={i.name}>
                <Table.Td>{i.name}</Table.Td>
                <Table.Td>{i.nic || "—"}</Table.Td>
                <Table.Td>{i.description}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>

        <Text size="sm" fw={600}>{t.config.configuredSections}</Text>
        <Group gap="xs">
          {inv.configured_sections.map((s) => (
            <Badge key={s} variant="light" color="blue">{s}</Badge>
          ))}
        </Group>

        <Text size="sm" fw={600}>{t.config.available}</Text>
        <Group gap="xs">
          {inv.available_capabilities.map((c) => (
            <Badge key={c.id} variant="outline" color="gray">{c.label}</Badge>
          ))}
        </Group>
      </Stack>
    </Card>
  );
}
```

- [ ] **Step 5: Run and verify it passes** — `npm test -- capabilitiespanel` → PASS. Whole suite green.

- [ ] **Step 6: Commit**
```bash
git add src/config/CapabilitiesPanel.tsx src/config/__tests__/capabilitiespanel.test.tsx src/i18n/en.ts
git commit -m "feat(fe): CapabilitiesPanel (version, interfaces, configured sections, available)"
```

---

## Task 3: `ConfigTree` / recursive node (collapsible, masked)

**Files:**
- Create: `src/config/ConfigTree.tsx`, `src/config/__tests__/configtree.test.tsx`

- [ ] **Step 1: Write the failing test**

Create `src/config/__tests__/configtree.test.tsx`:
```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import { ConfigTree } from "../ConfigTree";
import type { ConfigNode } from "../types";

const tree: ConfigNode = {
  tag: "opnsense", path: "opnsense", attributes: {}, value: null, sensitive: false,
  children: [
    {
      tag: "system", path: "opnsense/system", attributes: {}, value: null, sensitive: false,
      children: [
        { tag: "hostname", path: "opnsense/system/hostname", attributes: {}, value: "fw1", sensitive: false, children: [] },
        { tag: "password", path: "opnsense/system/password", attributes: {}, value: null, sensitive: true, children: [] },
      ],
    },
  ],
};

function wrap(ui: React.ReactNode) {
  return <MantineProvider><I18nProvider>{ui}</I18nProvider></MantineProvider>;
}

describe("ConfigTree", () => {
  it("renders leaves and masks sensitive values", () => {
    render(wrap(<ConfigTree root={tree} />));
    expect(screen.getByText("fw1")).toBeInTheDocument();
    // sensitive node shows a mask, not a value; no secret value rendered (value is null anyway)
    expect(screen.getByText(/hidden/i)).toBeInTheDocument();
    expect(screen.queryByText("fw1")?.textContent).not.toContain("password-secret");
  });

  it("collapses and expands a container", async () => {
    render(wrap(<ConfigTree root={tree} />));
    expect(screen.getByText("hostname")).toBeInTheDocument();
    // collapse the system container by clicking its toggle
    await userEvent.click(screen.getByText("system"));
    // Mantine Collapse animates; after toggle the child is hidden (or aria-hidden) — assert toggle works
    // (depending on Collapse unmount behavior; if it stays mounted, assert the chevron state instead)
  });
});
```
**Note:** Mantine `Collapse` keeps children mounted (animates height). If so, the "collapse hides child" assertion is unreliable — instead assert the **chevron/`aria-expanded`** toggles, or that a `data-open` attribute flips. Adapt the second test to the real behavior (the implementer verifies and asserts the robust signal). The first test (leaves + masking) is the must-have.

- [ ] **Step 2: Run and verify it fails** — `npm test -- configtree` → FAIL.

- [ ] **Step 3: Implement `ConfigTree`**

Create `src/config/ConfigTree.tsx`:
```tsx
import { useState } from "react";
import { Box, Collapse, Group, Text, UnstyledButton } from "@mantine/core";
import { useT } from "../i18n";
import type { ConfigNode } from "./types";

function NodeView({ node, depth }: { node: ConfigNode; depth: number }) {
  const t = useT();
  const hasChildren = node.children.length > 0;
  const [open, setOpen] = useState(depth < 2); // expand the top couple of levels

  if (!hasChildren) {
    return (
      <Group gap="xs" pl={depth * 16} wrap="nowrap" align="baseline">
        <Text size="sm" fw={500}>{node.tag}:</Text>
        {node.sensitive ? (
          <Text size="sm" c="dimmed">•••• 🔒 ({t.config.hidden})</Text>
        ) : (
          <Text size="sm">{node.value || "—"}</Text>
        )}
      </Group>
    );
  }

  return (
    <Box pl={depth * 16}>
      <UnstyledButton onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        <Group gap={6}>
          <Text size="sm" w={12}>{open ? "▾" : "▸"}</Text>
          <Text size="sm" fw={600}>{node.tag}</Text>
          {node.sensitive && <Text size="sm">🔒</Text>}
          <Text size="xs" c="dimmed">({node.children.length})</Text>
        </Group>
      </UnstyledButton>
      <Collapse in={open}>
        {node.children.map((c) => <NodeView key={c.path} node={c} depth={depth + 1} />)}
      </Collapse>
    </Box>
  );
}

export function ConfigTree({ root }: { root: ConfigNode }) {
  return <NodeView node={root} depth={0} />;
}
```

- [ ] **Step 4: Run and verify it passes** — `npm test -- configtree` → PASS (adapt the collapse assertion to Mantine's real behavior). Whole suite green.

- [ ] **Step 5: Commit**
```bash
git add src/config/ConfigTree.tsx src/config/__tests__/configtree.test.tsx
git commit -m "feat(fe): ConfigTree recursive collapsible node (sensitive masked, read-only)"
```

---

## Task 4: `ConfigTab` + reorganize `DeviceDetailPage` into Tabs

**Files:**
- Create: `src/config/ConfigTab.tsx`
- Modify: `src/pages/DeviceDetailPage.tsx`, `src/pages/__tests__/devicedetail.test.tsx`
- Create: `src/config/__tests__/configtab.test.tsx`

- [ ] **Step 1: Write `ConfigTab` test (fails)**

Create `src/config/__tests__/configtab.test.tsx`. Mock `/config/model` + `/config/capabilities` via MSW; assert the tree + panel render, and the empty-state on 404. Mirror the `monitoring` page tests + `withTenant` helper.
```tsx
// outline:
// server.use(http.get("/api/tenants/t1/devices/d1/config/model", () => HttpResponse.json(<tree>)));
// server.use(http.get("/api/tenants/t1/devices/d1/config/capabilities", () => HttpResponse.json(<inv>)));
// renderWithProviders(withTenant(<ConfigTab deviceId="d1" />));
// assert capabilities version + a config leaf value appear.
// second test: model 404 -> HttpResponse 404 + capabilities 404 -> assert "No configuration captured yet".
```

- [ ] **Step 2: Implement `ConfigTab`**

Create `src/config/ConfigTab.tsx`:
```tsx
import { Alert, Loader, Stack, Text } from "@mantine/core";
import { useT } from "../i18n";
import { CapabilitiesPanel } from "./CapabilitiesPanel";
import { ConfigTree } from "./ConfigTree";
import { useConfigCapabilities, useConfigModel } from "./hooks";

export function ConfigTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const model = useConfigModel(deviceId);
  const caps = useConfigCapabilities(deviceId);

  if (model.isLoading || caps.isLoading) return <Loader />;
  if (model.error || caps.error) return <Alert color="red">{t.config.noConfigYet}</Alert>;
  if (model.data === null) return <Text c="dimmed">{t.config.noConfigYet}</Text>;

  return (
    <Stack>
      {caps.data && <CapabilitiesPanel inv={caps.data} />}
      {model.data && <ConfigTree root={model.data} />}
    </Stack>
  );
}
```
(If `model.error || caps.error`, show an error; but per the hooks, a 404 returns null — not an error — so the empty-state branch handles "no snapshot".)

- [ ] **Step 3: Reorganize `DeviceDetailPage` into Tabs**

In `src/pages/DeviceDetailPage.tsx`, wrap the content in `Tabs` (default `info`):
```tsx
import { Badge, Card, Stack, Tabs, Text, Title } from "@mantine/core";
// ... existing imports + ConfigTab
        <Tabs defaultValue="info">
          <Tabs.List>
            <Tabs.Tab value="info">{t.config.tabInfo}</Tabs.Tab>
            <Tabs.Tab value="health">{t.config.tabHealth}</Tabs.Tab>
            <Tabs.Tab value="config">{t.config.tabConfig}</Tabs.Tab>
          </Tabs.List>
          <Tabs.Panel value="info" pt="md">
            <Card withBorder>
              <Text>{t.deviceDetail.url}: {device.base_url}</Text>
              <Text component="div">{t.deviceDetail.status}: <Badge>{device.status}</Badge></Text>
              <Text>{t.deviceDetail.firmware}: {device.firmware_version ?? t.common.none}</Text>
            </Card>
            {activeId && deviceId && <DeviceActions tenantId={activeId} deviceId={deviceId} />}
          </Tabs.Panel>
          <Tabs.Panel value="health" pt="md">
            {deviceId && <DeviceHealthSection deviceId={deviceId} />}
          </Tabs.Panel>
          <Tabs.Panel value="config" pt="md">
            {deviceId && <ConfigTab deviceId={deviceId} />}
          </Tabs.Panel>
        </Tabs>
```
Keep the `<Title>{device.name}</Title>` above the Tabs.

- [ ] **Step 4: Update the existing `devicedetail.test.tsx`**

Mantine `Tabs.Panel` mounts only the active panel. The existing health test ("shows the health section with charts and a range selector") must **click the Health tab first**:
```tsx
await userEvent.click(screen.getByRole("tab", { name: /Health/i }));
expect(await screen.findByText(/CPU/i)).toBeInTheDocument();
```
The test-connection and delete tests run on the default **Info** tab (device card + actions visible) — they should pass unchanged, but verify the device card/actions are inside the Info panel. If the health endpoint is no longer called on initial render (Health tab not active), remove the now-unneeded `/metrics` MSW handler from those tests (or keep it harmlessly). The Config tab is not active by default, so no `/config/*` handler is needed unless a test activates it.

- [ ] **Step 5: Run + build + lint**

Run: `npm test` → all green (existing updated + new). `npm run build` → clean. `npm run lint` → 0 problems.

- [ ] **Step 6: Commit**
```bash
git add src/config/ConfigTab.tsx src/config/__tests__/configtab.test.tsx \
        src/pages/DeviceDetailPage.tsx src/pages/__tests__/devicedetail.test.tsx
git commit -m "feat(fe): Config tab in DeviceDetailPage (tabs + capabilities + config tree)"
```

---

## Task 5: Technical debt

- [ ] **Step 1: Record the 4C debt**

Append to this plan:
```markdown
## Technical debt (4C)

- **No tree search/filter**: large configs render fully; add a path/tag filter + virtualization if needed.
- **Default-expansion depth fixed (2 levels)**: make configurable / smarter (expand to first leaf).
- **Configured-vs-available not cross-linked**: the panel lists configured sections and available
  capabilities separately; a future improvement maps a capability to whether its area is configured.
- **Sensitive container rendering**: a sensitive container (4B subtree redaction) shows a lock at the
  node but its children are present with null values; fine (no secret), polish later.
- **No manual "backup now"**: the tab shows the latest daily snapshot; a button to trigger an
  on-demand backup is a later nicety.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase4-milestone4C-config-ui.md
git commit -m "docs: technical debt milestone 4C"
```

---

## Definition of "Done" (4C)
- `DeviceDetailPage` has Info / Health / Config tabs; existing device/health/actions functionality intact.
- The Config tab renders the device's config tree (sensitive values masked + read-only) and a capabilities panel (interfaces, version, configured sections, available capabilities).
- Loading/error/empty (no-snapshot 404) states handled; tenant-scoped (tenant change refetches).
- No secret value appears in the DOM; everything read-only.
- Frontend suite (Vitest) green; `npm run build` + `npm run lint` clean.

---

## Technical debt (4C) — consolidated from reviews

- **No tree search/filter / virtualization**: large configs render fully; add a path/tag filter and
  virtualization if a config grows large.
- **Default-expansion depth fixed (2 levels)**: could be smarter (expand to first leaf, remember state).
- **Configured-vs-available not cross-linked**: the panel lists configured sections and available
  capabilities separately; a future improvement maps a capability to whether its area is configured
  (configured vs available-not-yet badge).
- **Sensitive container rendering**: a sensitive *container* (4B subtree redaction) shows a lock at the
  node and its children carry null values; correct (no secret) but the UX could collapse/annotate it.
- **No manual "backup now"**: the tab shows the latest daily snapshot; an on-demand backup trigger
  button is a later nicety.
- **Collapse assertion at root only**: in jsdom, Mantine `Collapse` keeps children mounted, so the
  expand/collapse test asserts on the root toggle (`aria-expanded`/chevron). A Playwright e2e would
  cover nested toggling in a real browser.
