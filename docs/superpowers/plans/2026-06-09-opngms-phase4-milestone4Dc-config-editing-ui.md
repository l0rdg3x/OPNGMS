# OPNGMS — Phase 4 / Milestone 4D-c: Config Editing UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator propose, preview, schedule (immediate or date/time), and cancel granular firewall **alias** changes from the Config tab, driving the 4D-a dry-run pipeline — with a Pending-changes panel showing pipeline status. Read-only users don't see the editing actions.

**Architecture:** Extends the 4C Config tab (React 19 + Mantine v9 + TanStack Query + typed client + i18n). New tenant-scoped hooks/mutations over the 4D-a change API, a `ChangesPanel`, a `ProposeAliasModal`, and inline preview/schedule/cancel controls (Mantine `@mantine/dates` `DateTimePicker`). No mutations touch a firewall (server-side pipeline is dry-run).

**Tech Stack:** React 19, Mantine v9 (+ `@mantine/dates`), TanStack Query v5, `openapi-fetch`, Vitest + RTL + MSW.

---

## Context for the implementer (read first)

Codebase is **English** — write all code/comments/UI strings (via i18n) in English. Phases 1–4D-a + 4C in `main`.

- **Config tab**: `src/config/ConfigTab.tsx` (mounts CapabilitiesPanel + ConfigTree; `ChangesPanel` is added in the main return). `src/config/hooks.ts` (4C hooks pattern: tenant-scoped, 404→null). `src/config/CapabilitiesPanel.tsx` (Mantine card/table style).
- **Mutation pattern**: `src/components/DeviceActions.tsx` — `useMutation` + `api.POST(...)` + `if (error) throw` + `onSuccess: invalidateQueries` + `notifications.show(...)`; `onError` → red notification. CSRF header auto-added by the client on POST.
- **i18n**: `src/i18n/en.ts` (`config` group exists; add a `changes` subgroup) + `useT()`. Error strings for hooks use `en.errors.*`.
- **Tenant/role**: `useTenant()` → `{ activeId, tenants }`; the active tenant's `role` is `tenants.find(t=>t.id===activeId)?.role` (`'tenant_admin'|'operator'|'read_only'|null`). Hide editing actions when role is `read_only`.
- **Tests**: `src/test/utils.tsx` (`renderWithProviders`), `src/config/__tests__/*` / `src/pages/__tests__/*` (MSW `server.use`, `withTenant` helper). MSW `onUnhandledRequest:"error"`.

**Commands** (from `frontend/`): `npm test`, `npm run build`, `npm run lint`. Schema regen: `npm run gen:api` (backend env — see prior plans). Current frontend suite: **24 tests green**.

**Security:** the change API never returns secrets (`ConfigChangeOut` hides payload/result; preview is secret-safe). The UI shows only those fields. Aliases carry no secrets. Read-only role hides editing actions (defense in depth; backend enforces `CONFIG_PUSH`).

⚠️ Alias `type`/`content` field shape TO VERIFY (4D-b); the form uses a plausible shape.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `package.json` | add `@mantine/dates` + `dayjs` | Modify (npm i) |
| `src/main.tsx` | import `@mantine/dates/styles.css` | Modify |
| `src/api/schema.d.ts` | regen (4D-a endpoints) | Regen |
| `src/config/changeTypes.ts` | `ConfigChange` TS type | Create |
| `src/config/changeHooks.ts` | list + create/schedule/cancel mutations + preview | Create |
| `src/config/ChangesPanel.tsx` | pending-changes table + actions | Create |
| `src/config/ProposeAliasModal.tsx` | create-change form | Create |
| `src/config/ConfigTab.tsx` | mount `ChangesPanel` | Modify |
| `src/i18n/en.ts` | `config.changes.*` + `errors.config*` strings | Modify |
| `src/config/__tests__/*` | tests | Create |

---

## Task 1: Data layer (schema regen + hooks/mutations + `@mantine/dates`)

**Files:**
- Modify: `package.json` (npm i), `src/main.tsx`; Regen: `src/api/schema.d.ts`
- Create: `src/config/changeTypes.ts`, `src/config/changeHooks.ts`
- Modify: `src/i18n/en.ts` (errors)

- [ ] **Step 1: Install `@mantine/dates`**
```bash
npm i @mantine/dates dayjs
```
In `src/main.tsx`, after the other Mantine CSS imports add: `import "@mantine/dates/styles.css";`.

- [ ] **Step 2: Regenerate the API types** — `npm run gen:api` (backend env). Confirm `/config/changes`, `/config/changes/{change_id}/preview`, `/schedule`, `/cancel` and `ConfigChangeIn`/`ScheduleIn`/`ConfigChangeOut` appear in `schema.d.ts`.

- [ ] **Step 3: `ConfigChange` type**

Create `src/config/changeTypes.ts`:
```ts
import type { components } from "../api/schema";

export type ConfigChange = components["schemas"]["ConfigChangeOut"];
```

- [ ] **Step 4: i18n error strings**

In `src/i18n/en.ts`, add to `errors`:
```ts
    configChangesLoad: "Failed to load changes",
    configChangeAction: "Action failed (you may lack permission)",
```

- [ ] **Step 5: Hooks + mutations**

Create `src/config/changeHooks.ts`:
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";
import type { ConfigChange } from "./changeTypes";

const listKey = (t: string | null, d: string | undefined) => ["config-changes", t, d];

export function useConfigChanges(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: listKey(activeId, deviceId),
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<ConfigChange[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (error) throw new Error(en.errors.configChangesLoad);
      return data ?? [];
    },
  });
}

export function useCreateChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { kind: string; operation: string; target: string; payload: Record<string, unknown> }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body },
      );
      if (error || !data) throw new Error(en.errors.configChangeAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey(activeId, deviceId) }),
  });
}

export function useScheduleChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, scheduled_at }: { id: string; scheduled_at: string | null }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/schedule",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, change_id: id } }, body: { scheduled_at } },
      );
      if (error || !data) throw new Error(en.errors.configChangeAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey(activeId, deviceId) }),
  });
}

export function useCancelChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/cancel",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, change_id: id } } },
      );
      if (error || !data) throw new Error(en.errors.configChangeAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey(activeId, deviceId) }),
  });
}

export function usePreviewChange(deviceId: string, changeId: string | null) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["config-change-preview", activeId, deviceId, changeId],
    enabled: !!activeId && !!changeId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/preview",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, change_id: changeId! } } },
      );
      if (error) throw new Error(en.errors.configChangeAction);
      return data;
    },
  });
}
```
(If a generated path-param name differs, align it. `usePreviewChange`/preview returns a free-form dict — type as needed.)

- [ ] **Step 6: Typecheck + suite** — `npm run build` clean; `npm test` (24) green.

- [ ] **Step 7: Commit**
```bash
git add package.json package-lock.json src/main.tsx src/api/schema.d.ts openapi.json \
        src/config/changeTypes.ts src/config/changeHooks.ts src/i18n/en.ts
git commit -m "feat(fe): config-change data layer (schema, list + create/schedule/cancel/preview hooks, @mantine/dates)"
```

---

## Task 2: `ChangesPanel` (list + status badges)

**Files:**
- Create: `src/config/ChangesPanel.tsx`, `src/config/__tests__/changespanel.test.tsx`
- Modify: `src/i18n/en.ts` (`config.changes.*`)

- [ ] **Step 1: i18n strings**

In `src/i18n/en.ts` `config` group, add a `changes` subgroup:
```ts
    changes: {
      title: "Pending changes",
      propose: "Propose alias change",
      none: "No pending changes",
      colKind: "Kind", colOperation: "Operation", colTarget: "Target",
      colStatus: "Status", colScheduled: "Scheduled",
      preview: "Preview", schedule: "Schedule", cancel: "Cancel",
      applyNow: "Apply now", pickTime: "Pick date/time",
      // form
      operation: "Operation", name: "Name", type: "Type", content: "Content (one per line)",
      create: "Create", add: "add", set: "set", delete: "delete",
    },
```

- [ ] **Step 2: Write the failing test**

Create `src/config/__tests__/changespanel.test.tsx`. MSW-mock `GET .../config/changes`; render `ChangesPanel` (via `renderWithProviders` + `withTenant`); assert the change rows + status badges; empty-state when `[]`; read-only hides the "Propose" button.
```tsx
// outline:
// server.use(http.get("/api/tenants/t1/devices/d1/config/changes", () => HttpResponse.json([change])));
// renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));   // tenant role tenant_admin
// expect propose button + the change target + a status badge.
// second test: role read_only -> no "Propose" button.
// third test: [] -> "No pending changes".
```

- [ ] **Step 3: Implement `ChangesPanel`**

Create `src/config/ChangesPanel.tsx` — table of changes with status badges + a header "Propose" button (opens the modal — Task 3) and per-row Preview/Schedule/Cancel (wired in Task 4). For Task 2, render the list + badges + the propose button (the modal/actions are added later — render a button that toggles state). Hide propose/actions when the active tenant role is `read_only`.
```tsx
import { useState } from "react";
import { Badge, Button, Card, Group, Table, Text, Title } from "@mantine/core";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import { useConfigChanges } from "./changeHooks";

const STATUS_COLOR: Record<string, string> = {
  draft: "gray", scheduled: "blue", applying: "yellow",
  applied: "green", conflict: "orange", failed: "red", cancelled: "gray",
};

export function ChangesPanel({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { activeId, tenants } = useTenant();
  const role = tenants.find((x) => x.id === activeId)?.role ?? null;
  const canEdit = role === "tenant_admin" || role === "operator";
  const q = useConfigChanges(deviceId);
  const [proposeOpen, setProposeOpen] = useState(false);

  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>{t.config.changes.title}</Title>
        {canEdit && <Button size="xs" onClick={() => setProposeOpen(true)}>{t.config.changes.propose}</Button>}
      </Group>
      {q.data && q.data.length === 0 && <Text c="dimmed">{t.config.changes.none}</Text>}
      {q.data && q.data.length > 0 && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.config.changes.colKind}</Table.Th>
              <Table.Th>{t.config.changes.colOperation}</Table.Th>
              <Table.Th>{t.config.changes.colTarget}</Table.Th>
              <Table.Th>{t.config.changes.colStatus}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.kind}</Table.Td>
                <Table.Td>{c.operation}</Table.Td>
                <Table.Td>{c.target}</Table.Td>
                <Table.Td><Badge color={STATUS_COLOR[c.status] ?? "gray"}>{c.status}</Badge></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {/* ProposeAliasModal mounted in Task 3/4; proposeOpen state ready */}
    </Card>
  );
}
```

- [ ] **Step 4: Run + commit** — `npm test -- changespanel` PASS; suite green; build/lint clean.
```bash
git add src/config/ChangesPanel.tsx src/config/__tests__/changespanel.test.tsx src/i18n/en.ts
git commit -m "feat(fe): ChangesPanel (pending changes list + status badges, read-only aware)"
```

---

## Task 3: `ProposeAliasModal` (create form)

**Files:**
- Create: `src/config/ProposeAliasModal.tsx`, `src/config/__tests__/proposealiasmodal.test.tsx`

- [ ] **Step 1: Write the failing test** — fill operation/name/type/content + submit → asserts `POST .../config/changes` called with `kind:"alias"`, `operation`, `target=name`, `payload:{name,type,content:[...]}`; content textarea split by newline; modal `onClose` called on success. MSW-mock the POST.

- [ ] **Step 2: Implement `ProposeAliasModal`**

Create `src/config/ProposeAliasModal.tsx` using `@mantine/form`:
```tsx
import { Button, Modal, SegmentedControl, Select, Stack, TextInput, Textarea } from "@mantine/core";
import { useForm } from "@mantine/form";
import { useT } from "../i18n";
import { useCreateChange } from "./changeHooks";

export function ProposeAliasModal({ deviceId, opened, onClose }: { deviceId: string; opened: boolean; onClose: () => void }) {
  const t = useT();
  const create = useCreateChange(deviceId);
  const form = useForm({
    initialValues: { operation: "set", name: "", type: "host", content: "" },
  });

  async function submit(v: typeof form.values) {
    const content = v.content.split("\n").map((s) => s.trim()).filter(Boolean);
    await create.mutateAsync({
      kind: "alias", operation: v.operation, target: v.name,
      payload: { name: v.name, type: v.type, content },
    });
    form.reset();
    onClose();
  }

  return (
    <Modal opened={opened} onClose={onClose} title={t.config.changes.propose}>
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          <SegmentedControl data={[
            { label: t.config.changes.add, value: "add" },
            { label: t.config.changes.set, value: "set" },
            { label: t.config.changes.delete, value: "delete" },
          ]} {...form.getInputProps("operation")} />
          <TextInput label={t.config.changes.name} required {...form.getInputProps("name")} />
          <Select label={t.config.changes.type} data={["host", "network", "port", "url"]} {...form.getInputProps("type")} />
          <Textarea label={t.config.changes.content} autosize minRows={3} {...form.getInputProps("content")} />
          <Button type="submit" loading={create.isPending}>{t.config.changes.create}</Button>
        </Stack>
      </form>
    </Modal>
  );
}
```

- [ ] **Step 3: Run + commit** — `npm test -- proposealiasmodal` PASS; suite green.
```bash
git add src/config/ProposeAliasModal.tsx src/config/__tests__/proposealiasmodal.test.tsx
git commit -m "feat(fe): ProposeAliasModal (create alias change form)"
```

---

## Task 4: Wire preview / schedule / cancel + mount in Config tab

**Files:**
- Modify: `src/config/ChangesPanel.tsx` (per-row actions + mount the modal), `src/config/ConfigTab.tsx` (mount ChangesPanel)
- Create: `src/config/__tests__/changesactions.test.tsx`

- [ ] **Step 1: Tests (fail)**

Create `src/config/__tests__/changesactions.test.tsx`. MSW-mock list + preview + schedule + cancel. Assert: clicking Preview shows the secret-safe summary; "Apply now" → schedule POST with `scheduled_at:null`; picking a date → `scheduled_at` sent; Cancel → cancel POST; a 403 on schedule → an error notification/`Alert`. Also a `devicedetail`/`configtab` test that the panel appears in the Config tab.

- [ ] **Step 2: Wire actions into `ChangesPanel`**

Add per-row actions (only for `draft`/`scheduled`, and only when `canEdit`): **Preview** (opens a modal with `usePreviewChange`), **Schedule** (a small popover/menu: "Apply now" → `useScheduleChange({id, scheduled_at:null})`; or a `DateTimePicker` (min now) → `scheduled_at: value.toISOString()`), **Cancel** (`useCancelChange(id)`). Mount `ProposeAliasModal` controlled by `proposeOpen`. On mutation error, show a red `notifications.show` (handles 403). Use `@mantine/dates` `DateTimePicker`.

- [ ] **Step 3: Mount in `ConfigTab`**

In `src/config/ConfigTab.tsx`, add `<ChangesPanel deviceId={deviceId} />` to the main return (after `ConfigTree`). (It renders when a config snapshot exists; that's acceptable — to propose a change you need a baseline.)

- [ ] **Step 4: Run + build + lint** — `npm test` all green; `npm run build` clean; `npm run lint` 0 problems.

- [ ] **Step 5: Commit**
```bash
git add src/config/ChangesPanel.tsx src/config/ConfigTab.tsx src/config/__tests__/changesactions.test.tsx
git commit -m "feat(fe): change actions (preview/schedule/cancel) + mount ChangesPanel in Config tab"
```

---

## Task 5: Technical debt

- [ ] **Step 1: Record the 4D-c debt**

Append:
```markdown
## Technical debt (4D-c)

- **Real push gated off** (4D-b): the pipeline is dry-run; the UI shows `applied`(dry-run)/`conflict`.
- **Alias type/content shape TO VERIFY** against the real OPNsense alias API (4D-b).
- **No live status polling**: the panel refetches on action; add `refetchInterval` for live status as the
  worker applies scheduled changes.
- **Conflict UX**: a `conflict` status shows a badge only; a "re-baseline & retry" flow is a later add.
- **ChangesPanel only renders with a config snapshot** (ConfigTab empty-state otherwise): acceptable
  (a baseline is needed to propose); show changes even without a snapshot later if needed.
- **Preview type loose** (`/preview` is a free-form dict): a typed preview schema would tighten it.
```

- [ ] **Step 2: Commit**
```bash
git add docs/superpowers/plans/2026-06-09-opngms-phase4-milestone4Dc-config-editing-ui.md
git commit -m "docs: technical debt milestone 4D-c"
```

---

## Definition of "Done" (4D-c)
- The Config tab shows a Pending-changes panel; an operator can propose an alias change, preview it (secret-safe), schedule it immediately or for a date/time, and cancel it; status badges reflect the pipeline.
- Read-only users don't see the editing actions; a 403 is handled gracefully; no secret value in the DOM.
- Tenant-scoped; frontend suite (Vitest) green; `npm run build` + `npm run lint` clean.
