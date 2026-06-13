# Plugin Coverage — Phase 4a Implementation Plan (per-device Plugins page)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A discoverable per-device **Plugins** tab that lists the plugins the box reports (from the Phase-2 telemetry), badges which are installed (+ version, locked), supports search, and lets an operator Install/Remove a plugin — reusing the existing firmware-action pipeline.

**Architecture:** Pure frontend. The data already exists: `GET /api/tenants/{tid}/devices/{did}/plugins` (Phase 2) returns `[{name, installed, version, locked}]`, and `useCreateFirmwareAction` (existing) POSTs `{kind: "plugin_install"|"plugin_remove", target}` to the existing, gated firmware-action endpoint. This phase adds a typed hook, a `PluginsTab` component, a tab on `DeviceDetailPage`, and i18n across all 12 locales. No backend change.

**Tech Stack:** React 19, Mantine v9, TanStack Query, `openapi-fetch` typed client, vitest + msw. Build gate: `npm run build` (`tsc -b` type-checks tests too).

**Branch:** `feat/plugin-ui` (already created off `main`).

**Spec:** `docs/superpowers/specs/2026-06-13-plugin-catalog-coverage-design.md` (Phase 4).

> **Scope note.** This is **4a** (the Plugins page). Editing a plugin's *configuration* in the catalog editor (merging the plugins catalog into the editor + a "Configure" deep-link) is **4b**, a separate PR. The existing raw text-input plugin install/remove in `src/firmware/FirmwareActions.tsx` is left as-is for now (the Plugins page is the nicer surface; a later cleanup can remove the duplicate once 4b lands).

**Run frontend commands from `frontend/`.** `npm ci --legacy-peer-deps` if deps aren't installed.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `frontend/src/api/schema.d.ts` | Generated OpenAPI types — regenerate so the `/plugins` endpoint + `PluginInfoOut` are present | Regenerate |
| `frontend/src/plugins/pluginsHooks.ts` | `useDevicePlugins(deviceId)` query hook | Create |
| `frontend/src/plugins/PluginsTab.tsx` | The Plugins list UI (badge/version/search/install/remove) | Create |
| `frontend/src/pages/DeviceDetailPage.tsx` | Add the "Plugins" tab + panel | Modify |
| `frontend/src/i18n/en.ts` | New `plugins` key group | Modify |
| `frontend/src/i18n/{it,es,fr,de,pt,nl,ru,ar,zh,zhTW,ja}.ts` | Mirror the `plugins` group (compiler-enforced parity) | Modify |
| `frontend/src/plugins/__tests__/pluginsTab.test.tsx` | Component tests (msw) | Create |

---

## Task 1: Regenerate the typed API client

The Phase-2 backend added `GET .../devices/{device_id}/plugins` returning `PluginInfoOut`. The committed `schema.d.ts` predates it, so regenerate it.

- [ ] **Step 1: Regenerate**

Run (from `frontend/`):
```bash
npm run gen:api
```
This runs the backend OpenAPI export + `openapi-typescript`. Expected: `src/api/schema.d.ts` updated.

- [ ] **Step 2: Verify the new types exist**

Run:
```bash
grep -n "devices/{device_id}/plugins" src/api/schema.d.ts
grep -n "PluginInfoOut" src/api/schema.d.ts
```
Expected: both match (the path operation + the `PluginInfoOut` schema with `name`/`installed`/`version`/`locked`).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/schema.d.ts frontend/openapi.json
git commit -m "chore(api): regenerate client types for the device plugins endpoint"
```

---

## Task 2: `useDevicePlugins` query hook

**Files:**
- Create: `frontend/src/plugins/pluginsHooks.ts`

Mirrors `src/firmware/hooks.ts`'s query style.

- [ ] **Step 1: Create the hook**

Create `frontend/src/plugins/pluginsHooks.ts`:

```tsx
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

export type PluginInfo = components["schemas"]["PluginInfoOut"];

/** The plugins the box last reported (installed + available), for the per-device Plugins tab. */
export function useDevicePlugins(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useQuery({
    queryKey: ["device-plugins", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<PluginInfo[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/plugins",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.plugins.loadFailed);
      return data;
    },
  });
}
```

- [ ] **Step 2: Type-check**

Run: `npx tsc -b`
Expected: no errors (confirms the generated path + `PluginInfoOut` type resolve). (`t.plugins.loadFailed` will error until Task 4 adds it — if so, do Task 4's `en.ts` + locales first, then return. Recommended order: Task 4 before this type-check, or accept the temporary error and resolve after Task 4.)

- [ ] **Step 3: Commit** (after Task 4 makes it type-clean)

```bash
git add frontend/src/plugins/pluginsHooks.ts
git commit -m "feat(plugins): useDevicePlugins query hook"
```

---

## Task 3: `PluginsTab` component

**Files:**
- Create: `frontend/src/plugins/PluginsTab.tsx`

Lists plugins (search-filterable), badges installed/locked + version, and per-row Install/Remove gated by `usePermissions().isOperator`, reusing `useCreateFirmwareAction`. A confirm step guards the write.

- [ ] **Step 1: Create the component**

Create `frontend/src/plugins/PluginsTab.tsx`:

```tsx
import { Badge, Button, Card, Group, Modal, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { useMemo, useState } from "react";
import { usePermissions } from "../auth/usePermissions";
import { useCreateFirmwareAction } from "../firmware/hooks";
import { useT } from "../i18n";
import { type PluginInfo, useDevicePlugins } from "./pluginsHooks";

/** Strip the `os-` package prefix for a friendlier display title (keep the full name as the id). */
function title(name: string): string {
  return name.startsWith("os-") ? name.slice(3) : name;
}

export function PluginsTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { isOperator: canWrite } = usePermissions();
  const plugins = useDevicePlugins(deviceId);
  const create = useCreateFirmwareAction(deviceId);
  const [search, setSearch] = useState("");
  const [confirm, setConfirm] = useState<{ kind: "plugin_install" | "plugin_remove"; name: string } | null>(null);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const list = (plugins.data ?? []).filter((p) => !q || p.name.toLowerCase().includes(q));
    // Installed first, then alphabetical.
    return [...list].sort((a, b) =>
      a.installed === b.installed ? a.name.localeCompare(b.name) : a.installed ? -1 : 1);
  }, [plugins.data, search]);

  async function run() {
    if (!confirm) return;
    await create.mutateAsync({ kind: confirm.kind, target: confirm.name });
    setConfirm(null);
    await plugins.refetch();
  }

  if (plugins.isError) {
    return <Text c="red" size="sm">{t.plugins.loadFailed}</Text>;
  }

  return (
    <Card withBorder>
      <Stack>
        <Group justify="space-between">
          <Title order={5}>{t.plugins.title}</Title>
          <TextInput
            placeholder={t.plugins.search}
            value={search}
            onChange={(e) => setSearch(e.currentTarget.value)}
            data-testid="plugins-search"
          />
        </Group>
        <Text c="dimmed" size="xs">{t.plugins.subtitle}</Text>
        <Table data-testid="plugins-list">
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.plugins.name}</Table.Th>
              <Table.Th>{t.plugins.status}</Table.Th>
              <Table.Th>{t.plugins.version}</Table.Th>
              <Table.Th />
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {rows.map((p: PluginInfo) => (
              <Table.Tr key={p.name}>
                <Table.Td>
                  <Text fw={500}>{title(p.name)}</Text>
                  <Text c="dimmed" size="xs">{p.name}</Text>
                </Table.Td>
                <Table.Td>
                  {p.installed
                    ? <Badge color="green" variant="light">{t.plugins.installed}</Badge>
                    : <Badge color="gray" variant="light">{t.plugins.notInstalled}</Badge>}
                  {p.locked && <Badge color="yellow" variant="light" ml="xs">{t.plugins.locked}</Badge>}
                </Table.Td>
                <Table.Td>{p.version || "—"}</Table.Td>
                <Table.Td>
                  {canWrite && !p.locked && (
                    p.installed
                      ? <Button size="xs" variant="light" color="red"
                          data-testid={`plugin-remove-${p.name}`}
                          onClick={() => setConfirm({ kind: "plugin_remove", name: p.name })}>
                          {t.plugins.remove}
                        </Button>
                      : <Button size="xs"
                          data-testid={`plugin-install-${p.name}`}
                          onClick={() => setConfirm({ kind: "plugin_install", name: p.name })}>
                          {t.plugins.install}
                        </Button>
                  )}
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
        {rows.length === 0 && <Text c="dimmed" size="sm">{t.plugins.empty}</Text>}
      </Stack>

      <Modal opened={confirm !== null} onClose={() => setConfirm(null)}
             title={confirm?.kind === "plugin_install" ? t.plugins.installConfirm : t.plugins.removeConfirm}>
        <Text size="sm" mb="md">{confirm ? title(confirm.name) : ""}</Text>
        <Group justify="flex-end">
          <Button variant="default" onClick={() => setConfirm(null)}>{t.common.cancel}</Button>
          <Button color={confirm?.kind === "plugin_remove" ? "red" : undefined}
                  loading={create.isPending} onClick={run} data-testid="plugin-confirm">
            {confirm?.kind === "plugin_install" ? t.plugins.install : t.plugins.remove}
          </Button>
        </Group>
      </Modal>
    </Card>
  );
}
```

> Verify `t.common.cancel` exists in `en.ts`; if not, add `cancel` to the `common` group (and all locales) in Task 4. (Most projects already have it — grep first: `grep -n "cancel" src/i18n/en.ts`.)

- [ ] **Step 2: Commit** (after Task 4 + type-check pass)

```bash
git add frontend/src/plugins/PluginsTab.tsx
git commit -m "feat(plugins): PluginsTab — list, badge install state, install/remove"
```

---

## Task 4: i18n — the `plugins` key group across 12 locales

**Files:**
- Modify: `frontend/src/i18n/en.ts` (+ the 11 sibling locales)

- [ ] **Step 1: Add the English keys**

In `frontend/src/i18n/en.ts`, add a `plugins` group (place it near the `firmware` group). Also confirm `common.cancel` exists; add it if missing.

```ts
  plugins: {
    tab: "Plugins",
    title: "Plugins",
    subtitle: "Plugins the firewall reports. Install or remove community plugins; the device must be on the latest firmware to install.",
    search: "Search plugins…",
    name: "Plugin",
    status: "Status",
    version: "Version",
    installed: "Installed",
    notInstalled: "Available",
    locked: "Locked",
    install: "Install",
    remove: "Remove",
    installConfirm: "Install this plugin?",
    removeConfirm: "Remove this plugin?",
    empty: "No plugins reported yet — the device is polled periodically.",
    loadFailed: "Could not load plugins.",
  },
```

- [ ] **Step 2: Mirror to all 11 other locales**

Add the same `plugins` group (translated) to each of `it.ts es.ts fr.ts de.ts pt.ts nl.ts ru.ts ar.ts zh.ts zhTW.ts ja.ts`. Keys must match `en.ts` exactly (the `Dict` type makes `tsc -b` fail otherwise). Translate the string values per locale; keep proper diacritics/scripts. (Use the project's prior translations as a style reference.)

- [ ] **Step 3: Type-check the whole project (the parity gate)**

Run: `npx tsc -b`
Expected: no errors — confirms every locale has the full `plugins` group and the hook/component keys resolve.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/i18n
git commit -m "i18n(plugins): Plugins tab strings across all 12 locales"
```

(Now go back and commit Task 2's hook + Task 3's component if not yet committed — the order that keeps each commit type-clean is: schema (T1) → i18n (T4) → hook (T2) → component (T3).)

---

## Task 5: Wire the Plugins tab into `DeviceDetailPage`

**Files:**
- Modify: `frontend/src/pages/DeviceDetailPage.tsx`

- [ ] **Step 1: Import + add the tab and panel**

In `frontend/src/pages/DeviceDetailPage.tsx`, add the import (with the other component imports):

```tsx
import { PluginsTab } from "../plugins/PluginsTab";
```

Add the tab in `<Tabs.List>` (after the `forwarding` tab):

```tsx
          <Tabs.Tab value="plugins">{t.plugins.tab}</Tabs.Tab>
```

Add the panel (after the `forwarding` panel):

```tsx
        <Tabs.Panel value="plugins" pt="md">
          {deviceId && <PluginsTab deviceId={deviceId} />}
        </Tabs.Panel>
```

- [ ] **Step 2: Build**

Run: `npm run build`
Expected: `tsc -b` + `vite build` succeed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/DeviceDetailPage.tsx
git commit -m "feat(plugins): mount the Plugins tab on the device detail page"
```

---

## Task 6: Component tests

**Files:**
- Create: `frontend/src/plugins/__tests__/pluginsTab.test.tsx`

- [ ] **Step 1: Write the tests**

Create `frontend/src/plugins/__tests__/pluginsTab.test.tsx`:

```tsx
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { PluginsTab } from "../PluginsTab";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const PLUGINS = "/api/tenants/t1/devices/d1/plugins";
const ACTION = "/api/tenants/t1/devices/d1/firmware/action";

function withTenant(node: ReactNode, role = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{ tenants: [{ id: "t1", name: "A", slug: "a", role }], activeId: "t1",
               setActiveId: () => {}, loading: false }}>
      {node}
    </TenantContext.Provider>
  );
}

const SAMPLE = [
  { name: "os-wireguard", installed: true, version: "2.6", locked: false },
  { name: "os-acme-client", installed: false, version: "4.16", locked: false },
];

describe("PluginsTab", () => {
  it("lists plugins and badges install state", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    expect(await screen.findByText("wireguard")).toBeInTheDocument();
    expect(screen.getByText("acme-client")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-remove-os-wireguard")).toBeInTheDocument();   // installed -> Remove
    expect(screen.getByTestId("plugin-install-os-acme-client")).toBeInTheDocument(); // available -> Install
  });

  it("install triggers a plugin_install firmware action", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    let posted: { kind?: string; target?: string } = {};
    server.use(http.post(ACTION, async ({ request }) => {
      posted = (await request.json()) as { kind?: string; target?: string };
      return HttpResponse.json({ id: "a1", kind: posted.kind, target: posted.target, status: "scheduled",
        result: {}, created_at: "2026-06-13T00:00:00Z", scheduled_at: null });
    }));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    await userEvent.click(await screen.findByTestId("plugin-install-os-acme-client"));
    await userEvent.click(await screen.findByTestId("plugin-confirm"));
    await waitFor(() => expect(posted).toEqual({ kind: "plugin_install", target: "os-acme-client" }));
  });

  it("hides write buttons for a read-only role", async () => {
    server.use(http.get(PLUGINS, () => HttpResponse.json(SAMPLE)));
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />, "read_only"));
    expect(await screen.findByText("wireguard")).toBeInTheDocument();
    expect(screen.queryByTestId("plugin-remove-os-wireguard")).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the tests**

Run: `npx vitest run src/plugins`
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/plugins/__tests__/pluginsTab.test.tsx
git commit -m "test(plugins): PluginsTab list, install action, and permission gating"
```

---

## Final verification (before opening the Phase 4a PR)

- [ ] **Build gate:** `cd frontend && npm run build` → success (`tsc -b` type-checks tests + all 12 locales).
- [ ] **Lint:** `cd frontend && npm run lint` → clean.
- [ ] **Tests:** `cd frontend && npx vitest run` → all green.
- [ ] Open the Phase 4a PR; CI green; squash-merge. Then **Phase 4b** (editor plugin-config: merge the plugins catalog into the device catalog endpoint + a "Configure" deep-link from this page) is the last plugin piece.

---

## Self-review notes (author)

- **Reuse, don't rebuild:** install/remove go through the EXISTING `useCreateFirmwareAction` → the gated `firmware/action` endpoint (`plugin_install`/`plugin_remove` kinds). No new backend.
- **Permissions:** write buttons gated by `usePermissions().isOperator` (operator+), matching `LogForwardingCard`. Locked plugins show no action.
- **`target`:** the firmware action `target` is the full telemetry `name` (`os-haproxy`) — what the connector's `core/firmware/install/{name}` expects.
- **i18n parity:** new `plugins` group added to en.ts first, then mirrored to all 11 locales (compiler-enforced by the `Dict` type — `npm run build` fails otherwise).
- **Type consistency:** `PluginInfo = components["schemas"]["PluginInfoOut"]` (`{name, installed, version, locked}`) is the single source for the hook, the component rows, and the tests.
