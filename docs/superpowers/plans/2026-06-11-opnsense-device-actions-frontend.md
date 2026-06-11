# OPNsense Device Actions — Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A device-detail UI to drive the (already-merged) firmware/plugin backend — a WebGUI deep-link button, a "Firmware" tab with an update/upgrade check + actions (now or scheduled, behind a confirm), a plugins install/remove form, and an auto-refreshing recent-actions list.

**Architecture:** Regenerate the typed openapi-fetch client to expose `/firmware/{check,action,actions}`; add TanStack Query hooks (one polling list query + two mutations); a `FirmwareActions` component composed of a check panel, a confirm+schedule modal reused by every action, a plugins form, and the actions list; mount it as a new tab on `DeviceDetailPage`. WebGUI button is a plain `target="_blank"` deep-link (true SSO is a separate milestone).

**Tech Stack:** Vite + React 19 + Mantine v9 + TanStack Query v5 + typed openapi-fetch + Vitest/RTL/MSW. English-only strings via `useT()` (typed `Dict` from `src/i18n/en.ts`).

**Spec:** `docs/superpowers/specs/2026-06-11-opnsense-device-actions-design.md` (§6 covers this UI; the WebGUI button + firmware/plugins UI).
**Branch:** `feat/opnsense-device-actions-frontend` (created).
**Backend (merged, on main):** `POST .../firmware/check` → `FirmwareCheckOut {status, updates:int, download_size, needs_reboot:bool, new_major:bool}`; `POST .../firmware/action` body `{kind, target, scheduled_at}` → `FirmwareActionOut {id, kind, target, status, scheduled_at, applied_at, result, created_at}`; `GET .../firmware/actions` → `FirmwareActionOut[]`. Kinds: `firmware_update | firmware_upgrade | plugin_install | plugin_remove`. There is **no** single-action GET — poll the list.

**Run frontend:** `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test` (Vitest). Lint/build: `npm run lint`, `npm run build`. English; commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

- **Modify:** `frontend/openapi.json` + `frontend/src/api/schema.d.ts` (regenerated), `frontend/src/i18n/en.ts` (add `firmware.*` + `deviceActions.openWebGui`), `frontend/src/components/DeviceActions.tsx` (WebGUI button), `frontend/src/pages/DeviceDetailPage.tsx` (Firmware tab).
- **Create:** `frontend/src/firmware/hooks.ts`, `frontend/src/firmware/FirmwareActions.tsx`, tests under `frontend/src/firmware/__tests__/` and `frontend/src/components/__tests__/`.

Conventions confirmed from the codebase: `import { api } from "../api/client"` (typed openapi-fetch; CSRF auto-injected by middleware); `import { useT } from "../i18n"` then `const t = useT()`; `useTenant().activeId` for tenant; query keys `[feature, activeId, deviceId]`; `notifications.show({...})` from `@mantine/notifications`; `ConfirmModal` exists but does not embed a picker (we build a dedicated modal); `DateTimePicker` from `@mantine/dates` (used in `src/config/ChangesPanel.tsx`); tests use `renderWithProviders` from `src/test/utils` + `server.use(http.METHOD(...))` from `src/test/server` (MSW).

---

## Task 1: Regenerate API types + add i18n strings

**Files:** Modify `frontend/openapi.json`, `frontend/src/api/schema.d.ts`, `frontend/src/i18n/en.ts`.

**Context:** The committed `openapi.json` predates the firmware endpoints. `npm run gen:api` runs the backend's `scripts/export_openapi.py` (needs `../backend/.venv`) → writes `openapi.json` → `openapi-typescript` → `schema.d.ts`. The backend is on `main` with the firmware routes, so regen will include them.

- [ ] **Step 1: Regenerate the typed client**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run gen:api`
Expected: exits 0; `openapi.json` and `src/api/schema.d.ts` change.

- [ ] **Step 2: Verify the firmware endpoints + schemas landed**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && rg -n "firmware/(check|action|actions)|FirmwareCheckOut|FirmwareActionOut|FirmwareActionIn" src/api/schema.d.ts | head`
Expected: matches for all three paths and the three schema names. If NONE appear, STOP and report (backend export failed or wrong venv) — do not hand-write types.

- [ ] **Step 3: Add i18n strings** — in `frontend/src/i18n/en.ts`, add a new `firmware` block (place it after the `deviceActions` block) and one key inside `deviceActions`. The `Dict` type is inferred from this file, so these keys become type-checked everywhere.

In the `deviceActions: { ... }` object add:
```ts
    openWebGui: "Open WebGUI",
```
Add a new top-level block (sibling of `deviceActions`):
```ts
  firmware: {
    title: "Firmware",
    tab: "Firmware",
    check: "Check for updates",
    checking: "Checking…",
    upToDate: "Up to date",
    updatesAvailable: "Updates available",
    downloadSize: "Download size",
    rebootNeeded: "Reboot needed",
    newMajor: "New major release available",
    update: "Update firmware",
    upgrade: "Upgrade to new major",
    updateConfirm: "Apply all pending package updates? The device may reboot.",
    upgradeConfirm:
      "Upgrade to the new major release? This runs multiple steps with reboots and can take a while.",
    plugins: "Plugins",
    pluginName: "Plugin name",
    install: "Install",
    remove: "Remove",
    installConfirm: "Install this plugin? The device must be on the latest firmware.",
    removeConfirm: "Remove this plugin?",
    runNow: "Run now",
    scheduleAt: "Schedule (leave empty to run now)",
    schedule: "Schedule",
    recentActions: "Recent actions",
    noActions: "No actions yet",
    actionQueued: "Action queued",
    actionFailed: "Could not queue the action",
    checkFailed: "Firmware check failed",
    kind: "Action",
    status: "Status",
    when: "When",
    result: "Result",
  },
```

- [ ] **Step 4: Typecheck + commit**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx tsc --noEmit` (or `npm run build` if that is the typecheck gate — check `package.json`; if `tsc -b` is the build, `npx tsc --noEmit` is fine for a quick check).
Expected: no type errors from `en.ts`.
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/openapi.json frontend/src/api/schema.d.ts frontend/src/i18n/en.ts
git commit -m "feat(fe): regenerate API types for firmware endpoints + i18n strings

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: WebGUI deep-link button

**Files:** Modify `frontend/src/components/DeviceActions.tsx`; Create `frontend/src/components/__tests__/deviceActionsWebgui.test.tsx`.

**Context:** `DeviceActions({ tenantId, deviceId })` renders a `Group` of buttons. It does NOT currently receive the device's `base_url`. Add an optional `baseUrl` prop and render an "Open WebGUI" button that opens it in a new tab (`component="a"`, `target="_blank"`, `rel="noopener noreferrer"`). True SSO is deferred — this is a plain deep-link to the WebGUI login page.

- [ ] **Step 1: Write the failing test** `frontend/src/components/__tests__/deviceActionsWebgui.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { DeviceActions } from "../DeviceActions";
import { renderWithProviders } from "../../test/utils";

describe("DeviceActions WebGUI link", () => {
  it("renders an Open WebGUI link to the device base_url in a new tab", () => {
    renderWithProviders(
      <DeviceActions tenantId="t1" deviceId="d1" baseUrl="https://192.168.1.82" />,
    );
    const link = screen.getByTestId("btn-webgui") as HTMLAnchorElement;
    expect(link.getAttribute("href")).toBe("https://192.168.1.82");
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
  });

  it("omits the WebGUI link when no base_url is given", () => {
    renderWithProviders(<DeviceActions tenantId="t1" deviceId="d1" />);
    expect(screen.queryByTestId("btn-webgui")).toBeNull();
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/components/__tests__/deviceActionsWebgui.test.tsx`
Expected: FAIL (no `btn-webgui`; prop type error on `baseUrl`).

- [ ] **Step 3: Implement** — in `frontend/src/components/DeviceActions.tsx`:
  - Change the signature to accept an optional `baseUrl`:
    ```tsx
    export function DeviceActions({
      tenantId,
      deviceId,
      baseUrl,
    }: { tenantId: string; deviceId: string; baseUrl?: string }) {
    ```
  - In the `<Group mt="md">`, add as the first button (before "Test connection"):
    ```tsx
        {baseUrl && (
          <Button
            component="a"
            href={baseUrl}
            target="_blank"
            rel="noopener noreferrer"
            variant="light"
            data-testid="btn-webgui"
          >
            {t.deviceActions.openWebGui}
          </Button>
        )}
    ```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/components/__tests__/deviceActionsWebgui.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/components/DeviceActions.tsx frontend/src/components/__tests__/deviceActionsWebgui.test.tsx
git commit -m "feat(fe): Open WebGUI deep-link button on device actions

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Firmware data hooks

**Files:** Create `frontend/src/firmware/hooks.ts`, `frontend/src/firmware/__tests__/hooks.test.tsx`.

**Context:** Mirror the existing hook conventions (`src/config/hooks.ts`, `src/config/changeHooks.ts`): `useTenant().activeId`, query keys `[feature, activeId, deviceId]`, `api.GET/POST` with `{ params: { path: {...} }, body }`, invalidate on mutation success. CSRF is auto-injected. The actions list query polls (`refetchInterval`) while any action is non-terminal (`scheduled`/`running`).

- [ ] **Step 1: Write the failing test** `frontend/src/firmware/__tests__/hooks.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { useCreateFirmwareAction, useFirmwareActions } from "../hooks";

// MSW URLs are RELATIVE — the openapi-fetch client uses baseUrl "" in tests (VITE_API_BASE unset).
const BASE = "/api/tenants/t1/devices/d1/firmware";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <QueryClientProvider client={qc}>
        <TenantContext.Provider
          value={{
            tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
            activeId: "t1",
            setActiveId: () => {},
            loading: false,
          }}
        >
          {children}
        </TenantContext.Provider>
      </QueryClientProvider>
    </I18nProvider>
  );
}

describe("firmware hooks", () => {
  it("useFirmwareActions loads the actions list", async () => {
    server.use(
      http.get(`${BASE}/actions`, () =>
        HttpResponse.json([
          { id: "a1", kind: "firmware_update", target: "", status: "done",
            scheduled_at: null, applied_at: null, result: { version: "26.1.9" },
            created_at: "2026-06-11T00:00:00Z" },
        ]),
      ),
    );
    const { result } = renderHook(() => useFirmwareActions("d1"), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].kind).toBe("firmware_update");
  });

  it("useCreateFirmwareAction POSTs the body and returns the created action", async () => {
    let captured: unknown = null;
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        captured = await request.json();
        return HttpResponse.json(
          { id: "a2", kind: "plugin_install", target: "os-acme-client", status: "scheduled",
            scheduled_at: null, applied_at: null, result: {}, created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    const { result } = renderHook(() => useCreateFirmwareAction("d1"), { wrapper });
    const created = await result.current.mutateAsync({
      kind: "plugin_install", target: "os-acme-client", scheduled_at: null,
    });
    expect(created.id).toBe("a2");
    expect(captured).toMatchObject({ kind: "plugin_install", target: "os-acme-client" });
  });
});
```
NOTE (verified): the tenant comes from `TenantContext` (`src/tenant/TenantProvider`); the wrapper above is the real shape used across the codebase (see `src/config/__tests__/configtab.test.tsx`, `src/pages/__tests__/devicedetail.test.tsx`). `useTenant` is `useContext(TenantContext)`. The hook test wrapper also needs `I18nProvider` because the mutation hooks call `useT()`. MSW URLs are relative because the client's `baseUrl` is `""` in tests.

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/firmware/__tests__/hooks.test.tsx`
Expected: FAIL (module `../hooks` not found).

- [ ] **Step 3: Implement** `frontend/src/firmware/hooks.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type FirmwareAction = components["schemas"]["FirmwareActionOut"];
export type FirmwareCheck = components["schemas"]["FirmwareCheckOut"];
export type FirmwareActionIn = components["schemas"]["FirmwareActionIn"];

const TERMINAL = new Set(["done", "failed"]);

/** Poll the actions list while any action is still scheduled/running. */
export function useFirmwareActions(deviceId: string) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["firmware-actions", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    refetchInterval: (query) => {
      const rows = (query.state.data as FirmwareAction[] | undefined) ?? [];
      return rows.some((r) => !TERMINAL.has(r.status)) ? 3000 : false;
    },
    queryFn: async (): Promise<FirmwareAction[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/firmware/actions",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error("firmware actions load failed");
      return data;
    },
  });
}

/** "Check for updates" — POST returns the current update picture. */
export function useFirmwareCheck(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (): Promise<FirmwareCheck> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/firmware/check",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.firmware.checkFailed);
      return data;
    },
  });
}

/** Create a firmware/plugin action (now if scheduled_at is null, else deferred). */
export function useCreateFirmwareAction(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (body: FirmwareActionIn): Promise<FirmwareAction> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/firmware/action",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body },
      );
      if (error || !data) throw new Error(t.firmware.actionFailed);
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["firmware-actions", activeId, deviceId] }),
  });
}
```
NOTE: confirm the generated schema export name is `components["schemas"]["FirmwareActionOut"]` etc. by checking `src/api/schema.d.ts` (Task 1). If `openapi-typescript` named the import differently (it exports `components` and `paths`), keep `components`. If `FirmwareActionIn` requires `target`/`scheduled_at` as required fields and that fights the mutation call sites, set them explicitly at the call sites (Task 4 always passes all three). The `refetchInterval` callback receives the `query` object in TanStack Query v5 — if the installed types expect `(query) => number | false` use as written; if they pass the data directly, adapt (check the v5 signature in `node_modules/@tanstack/react-query` or an existing `refetchInterval` usage; there is none in-repo, so follow v5: `refetchInterval` accepts a function `(query) => ...`).

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/firmware/__tests__/hooks.test.tsx`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/firmware/hooks.ts frontend/src/firmware/__tests__/hooks.test.tsx
git commit -m "feat(fe): firmware data hooks (poll actions, check, create action)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: FirmwareActions component

**Files:** Create `frontend/src/firmware/FirmwareActions.tsx`, `frontend/src/firmware/__tests__/firmwareActions.test.tsx`.

**Context:** One component with four parts: (1) a check panel — "Check for updates" runs `useFirmwareCheck`, then shows the result and enables Update / Upgrade; (2) a confirm+schedule modal reused by every action (a description + an optional `DateTimePicker` — empty = now, a date = `scheduled_at`); (3) a plugins form (name input + Install/Remove); (4) the recent-actions list (`useFirmwareActions`, auto-refreshing). All four actions funnel through one modal+`useCreateFirmwareAction`.

- [ ] **Step 1: Write the failing test** `frontend/src/firmware/__tests__/firmwareActions.test.tsx`:
```tsx
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { FirmwareActions } from "../FirmwareActions";

// Relative URL: client baseUrl is "" in tests.
const BASE = "/api/tenants/t1/devices/d1/firmware";

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

function listOnce(rows: unknown[] = []) {
  server.use(http.get(`${BASE}/actions`, () => HttpResponse.json(rows)));
}

describe("FirmwareActions", () => {
  it("runs a firmware check and shows the result", async () => {
    listOnce();
    server.use(
      http.post(`${BASE}/check`, () =>
        HttpResponse.json({
          status: "ok", updates: 3, download_size: "12M", needs_reboot: true, new_major: false,
        }),
      ),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.click(screen.getByTestId("btn-fw-check"));
    await screen.findByText(/Updates available/i);
    expect(screen.getByTestId("btn-fw-update")).toBeEnabled();
  });

  it("confirms and queues a firmware_update (run now)", async () => {
    listOnce();
    server.use(
      http.post(`${BASE}/check`, () =>
        HttpResponse.json({ status: "ok", updates: 1, download_size: "1M", needs_reboot: false, new_major: false }),
      ),
    );
    const posted = vi.fn();
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json(
          { id: "a1", kind: "firmware_update", target: "", status: "scheduled",
            scheduled_at: null, applied_at: null, result: {}, created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.click(screen.getByTestId("btn-fw-check"));
    await screen.findByText(/Updates available/i);
    await userEvent.click(screen.getByTestId("btn-fw-update"));
    // confirm modal -> Run now
    await userEvent.click(await screen.findByTestId("btn-fw-confirm-now"));
    await waitFor(() =>
      expect(posted).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "firmware_update", scheduled_at: null }),
      ),
    );
  });

  it("installs a plugin by name", async () => {
    listOnce();
    const posted = vi.fn();
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json(
          { id: "a2", kind: "plugin_install", target: "os-acme-client", status: "scheduled",
            scheduled_at: null, applied_at: null, result: {}, created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.type(screen.getByTestId("input-plugin-name"), "os-acme-client");
    await userEvent.click(screen.getByTestId("btn-plugin-install"));
    await userEvent.click(await screen.findByTestId("btn-fw-confirm-now"));
    await waitFor(() =>
      expect(posted).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "plugin_install", target: "os-acme-client" }),
      ),
    );
  });

  it("renders recent actions from the list endpoint", async () => {
    listOnce([
      { id: "a9", kind: "plugin_remove", target: "os-foo", status: "done",
        scheduled_at: null, applied_at: null, result: { version: "26.1.9" },
        created_at: "2026-06-11T00:00:00Z" },
    ]);
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    const list = await screen.findByTestId("fw-actions-list");
    expect(within(list).getByText(/plugin_remove/)).toBeInTheDocument();
    expect(within(list).getByText(/done/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/firmware/__tests__/firmwareActions.test.tsx`
Expected: FAIL (module `../FirmwareActions` not found).

- [ ] **Step 3: Implement** `frontend/src/firmware/FirmwareActions.tsx`:
```tsx
import { Badge, Button, Card, Group, Modal, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { DateTimePicker } from "@mantine/dates";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { useT } from "../i18n";
import {
  type FirmwareActionIn,
  useCreateFirmwareAction,
  useFirmwareActions,
  useFirmwareCheck,
} from "./hooks";

type Kind = FirmwareActionIn["kind"];
type Pending = { kind: Kind; target: string; confirm: string };

export function FirmwareActions({ deviceId }: { deviceId: string }) {
  const t = useT();
  const check = useFirmwareCheck(deviceId);
  const create = useCreateFirmwareAction(deviceId);
  const actions = useFirmwareActions(deviceId);
  const [pluginName, setPluginName] = useState("");
  const [pending, setPending] = useState<Pending | null>(null);
  const [when, setWhen] = useState<string | null>(null);

  const result = check.data;
  const hasUpdates = !!result && (result.status.toLowerCase() === "ok" || result.updates > 0);

  function open(kind: Kind, target: string, confirm: string) {
    setWhen(null);
    setPending({ kind, target, confirm });
  }

  async function fire(scheduled: boolean) {
    if (!pending) return;
    const body: FirmwareActionIn = {
      kind: pending.kind,
      target: pending.target,
      scheduled_at: scheduled ? when : null,
    };
    setPending(null);
    try {
      await create.mutateAsync(body);
      notifications.show({ message: t.firmware.actionQueued });
    } catch {
      notifications.show({ color: "red", message: t.firmware.actionFailed });
    }
  }

  return (
    <Stack mt="md">
      <Card withBorder>
        <Group justify="space-between" mb="xs">
          <Title order={5}>{t.firmware.title}</Title>
          <Button size="xs" onClick={() => check.mutate()} loading={check.isPending} data-testid="btn-fw-check">
            {t.firmware.check}
          </Button>
        </Group>
        {result && (
          <Stack gap={4}>
            <Text data-testid="fw-check-result">
              {hasUpdates ? t.firmware.updatesAvailable : t.firmware.upToDate}
              {result.updates > 0 ? ` (${result.updates})` : ""}
            </Text>
            <Text size="sm" c="dimmed">
              {t.firmware.downloadSize}: {result.download_size || t.common.none} · {t.firmware.rebootNeeded}:{" "}
              {result.needs_reboot ? "yes" : "no"}
            </Text>
            <Group mt="xs">
              <Button
                size="xs"
                disabled={!hasUpdates}
                onClick={() => open("firmware_update", "", t.firmware.updateConfirm)}
                data-testid="btn-fw-update"
              >
                {t.firmware.update}
              </Button>
              {result.new_major && (
                <Button
                  size="xs"
                  color="orange"
                  onClick={() => open("firmware_upgrade", "", t.firmware.upgradeConfirm)}
                  data-testid="btn-fw-upgrade"
                >
                  {t.firmware.upgrade}
                </Button>
              )}
            </Group>
          </Stack>
        )}
      </Card>

      <Card withBorder>
        <Title order={5} mb="xs">{t.firmware.plugins}</Title>
        <Group align="flex-end">
          <TextInput
            label={t.firmware.pluginName}
            value={pluginName}
            onChange={(e) => setPluginName(e.currentTarget.value)}
            data-testid="input-plugin-name"
          />
          <Button
            size="sm"
            disabled={!pluginName.trim()}
            onClick={() => open("plugin_install", pluginName.trim(), t.firmware.installConfirm)}
            data-testid="btn-plugin-install"
          >
            {t.firmware.install}
          </Button>
          <Button
            size="sm"
            variant="light"
            color="red"
            disabled={!pluginName.trim()}
            onClick={() => open("plugin_remove", pluginName.trim(), t.firmware.removeConfirm)}
            data-testid="btn-plugin-remove"
          >
            {t.firmware.remove}
          </Button>
        </Group>
      </Card>

      <Card withBorder>
        <Title order={5} mb="xs">{t.firmware.recentActions}</Title>
        {actions.data && actions.data.length > 0 ? (
          <Table data-testid="fw-actions-list">
            <Table.Thead>
              <Table.Tr>
                <Table.Th>{t.firmware.kind}</Table.Th>
                <Table.Th>{t.firmware.status}</Table.Th>
                <Table.Th>{t.firmware.when}</Table.Th>
                <Table.Th>{t.firmware.result}</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {actions.data.map((a) => (
                <Table.Tr key={a.id}>
                  <Table.Td>{a.kind}{a.target ? `: ${a.target}` : ""}</Table.Td>
                  <Table.Td><Badge variant="light">{a.status}</Badge></Table.Td>
                  <Table.Td>{a.scheduled_at ?? a.created_at}</Table.Td>
                  <Table.Td>{a.result?.version ?? a.result?.error ?? ""}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        ) : (
          <Text c="dimmed" size="sm">{t.firmware.noActions}</Text>
        )}
      </Card>

      <Modal
        opened={!!pending}
        onClose={() => setPending(null)}
        title={t.confirm.title}
        data-testid="fw-confirm-modal"
        transitionProps={{ duration: 0 }}
      >
        <Stack>
          <Text>{pending?.confirm}</Text>
          <DateTimePicker
            label={t.firmware.scheduleAt}
            value={when}
            onChange={setWhen}
            minDate={new Date()}
            clearable
            data-testid="fw-schedule-picker"
          />
          <Group justify="flex-end">
            <Button variant="default" onClick={() => setPending(null)} data-testid="btn-fw-cancel">
              {t.confirm.cancel}
            </Button>
            <Button
              variant="light"
              onClick={() => fire(false)}
              loading={create.isPending}
              data-testid="btn-fw-confirm-now"
            >
              {t.firmware.runNow}
            </Button>
            <Button
              onClick={() => fire(true)}
              disabled={!when}
              loading={create.isPending}
              data-testid="btn-fw-confirm-schedule"
            >
              {t.firmware.schedule}
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Stack>
  );
}
```
NOTES for the implementer:
- `result.result?.version` / `.error`: `FirmwareActionOut.result` is typed as an object (JSONB). If the generated type is `Record<string, never>` or `{ [k: string]: unknown }`, accessing `.version`/`.error` may need a cast like `(a.result as { version?: string; error?: string })`. Adjust to satisfy `tsc` without `any` where possible.
- `DateTimePicker`'s `value`/`onChange` type: in `@mantine/dates` v9 these are `string | null` (confirmed by `src/config/ChangesPanel.tsx`). Match that file's exact prop types; if it uses `Date | null`, follow that and convert to ISO when building the body (`when ? new Date(when).toISOString() : null`).
- `Kind` derives from the generated `FirmwareActionIn["kind"]`. If that field is a plain `string` (not a union) in the schema, define `type Kind = "firmware_update" | "firmware_upgrade" | "plugin_install" | "plugin_remove"` locally instead.
- DECIDED: `FirmwareActions` takes ONLY `deviceId` (no `tenantId` prop) — the hooks read the tenant from `useTenant`, so a `tenantId` prop would be unused (lint `no-unused-vars`). Task 5 mounts `<FirmwareActions deviceId={deviceId} />` and the tests render `withTenant(<FirmwareActions deviceId="d1" />)`. Keep this consistent.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/firmware/__tests__/firmwareActions.test.tsx`
Expected: PASS (4 tests). Debug real failures; do not weaken assertions.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/firmware/FirmwareActions.tsx frontend/src/firmware/__tests__/firmwareActions.test.tsx
git commit -m "feat(fe): FirmwareActions component (check, update/upgrade, plugins, actions list)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Mount on DeviceDetailPage (Firmware tab + WebGUI button)

**Files:** Modify `frontend/src/pages/DeviceDetailPage.tsx`; Create `frontend/src/pages/__tests__/deviceDetailFirmwareTab.test.tsx`.

**Context:** Add a fourth tab "Firmware" rendering `<FirmwareActions deviceId={deviceId} />` (or `tenantId+deviceId` if you kept that prop), and pass `baseUrl={device.base_url}` to the existing `<DeviceActions>` so the WebGUI button appears.

- [ ] **Step 1: Write the failing test** `frontend/src/pages/__tests__/deviceDetailFirmwareTab.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { DeviceDetailPage } from "../DeviceDetailPage";

// Relative URLs (client baseUrl is "" in tests). Routing + tenant mirror
// the existing src/pages/__tests__/devicedetail.test.tsx exactly.
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

const device = {
  id: "d1", tenant_id: "t1", name: "fw1", base_url: "https://192.168.1.82", verify_tls: true,
  tls_fingerprint: null, site: null, tags: [], status: "reachable", last_seen: null,
  firmware_version: "26.1.9", created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z",
};

describe("DeviceDetailPage firmware tab", () => {
  it("shows a Firmware tab and the WebGUI link", async () => {
    server.use(
      http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
      http.get("/api/tenants/t1/devices/d1/firmware/actions", () => HttpResponse.json([])),
    );
    renderWithProviders(
      withTenant(
        <Routes>
          <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
        </Routes>,
      ),
      { route: "/devices/d1" },
    );
    // WebGUI link present on the Info tab
    expect((await screen.findByTestId("btn-webgui")).getAttribute("href")).toBe("https://192.168.1.82");
    // switch to the Firmware tab (inactive Tabs.Panel mounts lazily on activation)
    await userEvent.click(screen.getByRole("tab", { name: /Firmware/i }));
    expect(await screen.findByTestId("btn-fw-check")).toBeInTheDocument();
  });
});
```
NOTE (verified): this mirrors `src/pages/__tests__/devicedetail.test.tsx` — relative MSW URLs, `withTenant` wrapping `TenantContext.Provider`, and the page rendered inside `<Routes><Route path="/devices/:deviceId" ...>` with `{ route: "/devices/d1" }`. Inactive `Tabs.Panel`s mount only on activation (that test's Health case proves it: "activate it first so the panel mounts"), so the Firmware GET fires only after the tab is clicked — the existing devicedetail tests that don't open the Firmware tab need no new handler.

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/pages/__tests__/deviceDetailFirmwareTab.test.tsx`
Expected: FAIL (no Firmware tab / no `btn-webgui`).

- [ ] **Step 3: Implement** — edit `frontend/src/pages/DeviceDetailPage.tsx`:
  - Add the import: `import { FirmwareActions } from "../firmware/FirmwareActions";`
  - Pass `baseUrl` to `DeviceActions`:
    ```tsx
            {activeId && deviceId && (
              <DeviceActions tenantId={activeId} deviceId={deviceId} baseUrl={device.base_url} />
            )}
    ```
  - Add a new tab trigger in `<Tabs.List>` (after the `config` tab):
    ```tsx
            <Tabs.Tab value="firmware">{t.firmware.tab}</Tabs.Tab>
    ```
  - Add the panel (after the `config` panel):
    ```tsx
          <Tabs.Panel value="firmware" pt="md">
            {deviceId && <FirmwareActions deviceId={deviceId} />}
          </Tabs.Panel>
    ```

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npx vitest run src/pages/__tests__/deviceDetailFirmwareTab.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/pages/DeviceDetailPage.tsx frontend/src/pages/__tests__/deviceDetailFirmwareTab.test.tsx
git commit -m "feat(fe): Firmware tab + WebGUI button on device detail page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full frontend suite green: `cd frontend && npm test`
- [ ] Lint clean: `cd frontend && npm run lint`
- [ ] Build passes: `cd frontend && npm run build`
- [ ] Dispatch a final holistic review, then superpowers:finishing-a-development-branch → PR to protected `main` (green checks required).
- [ ] After merge: update `README.md` roadmap (device actions UI shipped) per the keep-README-updated convention.

---

## Self-Review (author)

**Spec coverage (UI portion, design §6):** WebGUI deep-link button (Task 2 + 5); firmware update/upgrade with a confirm gate + now/scheduled (Task 4, the confirm+schedule modal); plugin install/remove form (Task 4); recent-actions visibility with auto-refresh polling the list (Task 3 `refetchInterval` + Task 4 list). SSO explicitly deferred (the button is a plain deep-link). The firmware "check" surfaces the update picture (updates/download/reboot/new-major) to decide which actions to offer.

**Placeholder scan:** every code step is complete; the three "confirm the real provider/route/schema name by reading X" notes name a concrete file to copy from + a defined fallback, not a vague TODO. Type-cast guidance for the JSONB `result` and the `DateTimePicker` value type is concrete.

**Type consistency:** `FirmwareAction`/`FirmwareCheck`/`FirmwareActionIn` are defined once in `hooks.ts` (from the generated `components["schemas"][...]`) and reused by the component; the three endpoint paths match the merged backend; `useCreateFirmwareAction` body `{kind, target, scheduled_at}` matches `FirmwareActionIn` and the component's `fire()`; query key `["firmware-actions", activeId, deviceId]` is identical in the list query and the mutation's invalidation; `data-testid`s used in tests (`btn-fw-check`, `btn-fw-update`, `btn-fw-confirm-now`, `input-plugin-name`, `btn-plugin-install`, `fw-actions-list`, `btn-webgui`) all exist in the components.
