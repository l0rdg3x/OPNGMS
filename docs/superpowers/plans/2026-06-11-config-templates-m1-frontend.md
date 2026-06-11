# Configuration Templates — M1 Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The UI for the (merged) configuration-template engine M1: a superadmin-only **Template Library** page (CRUD `firewall_alias` templates) and a per-device **Apply template** flow (pick a library template → optionally edit this customer's override → preview the redacted effective body → apply now or scheduled, reusing the device-actions confirm+schedule modal).

**Architecture:** Regenerate the typed openapi-fetch client for `/api/templates*`; a `useIsSuperadmin` gate (first superadmin-gated UI — mirrors the existing `tenant_admin` role gate); library CRUD hooks + a `TemplateLibraryPage` (list + create/edit/delete modal, `content: string[]` edited as a newline textarea like `ProposeAliasModal`); apply hooks (override upsert / preview / apply) + an `ApplyTemplateTab` mounted as a new "Templates" tab on `DeviceDetailPage`, reusing the `FirmwareActions` confirm+schedule modal.

**Tech Stack:** Vite + React 19 + Mantine v9 + TanStack Query v5 + typed openapi-fetch + Vitest/RTL/MSW. English-only via `useT()`.

**Spec:** `docs/superpowers/specs/2026-06-11-config-templates-m1-design.md` (§4.5 the UI).
**Branch:** `feat/config-templates-m1-frontend` (created).
**Backend (merged on main):** `GET /api/templates` (any auth), `POST /api/templates` (201, superadmin), `PUT /api/templates/{id}` (superadmin), `DELETE /api/templates/{id}` (204, superadmin); `PUT /api/tenants/{tid}/templates/{id}/override`; `POST /api/tenants/{tid}/devices/{did}/templates/{id}/preview` → `TemplatePreviewOut{operation,kind,target,new}`; `POST /api/tenants/{tid}/devices/{did}/templates/{id}/apply` → `{change_id,status}`. `firewall_alias` body = `{name, type, content: string[], description}`; an override `body_patch` may change `content`/`description` (name/type are pinned server-side).

**Run:** `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test` (Vitest); `npm run lint`; `npm run build`. English; commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Conventions (verified)
- `import { api } from "../api/client"` (CSRF auto-injected); `import { useT } from "../i18n"`; `useTenant()` → `{ activeId, tenants }`; `useAuth()` (`src/auth/useAuth.ts`) → `{ me }` with `me.is_superadmin`.
- Query hooks: key `[feature, ...ids]`, `api.GET/POST/PUT/DELETE`, invalidate on mutation success.
- `ConfirmModal` (`src/components/ConfirmModal.tsx`) for deletes; Mantine `useForm` for forms (see `DeviceCreateModal.tsx`); `content: string[]` edited as a `Textarea` split on `\n` (see `src/config/ProposeAliasModal.tsx`).
- Nav/routes in `src/components/AppShell.tsx`; existing role gate `{role === "tenant_admin" && <NavLink .../>}`; in-page gate returns an `<Alert>` (see `src/pages/ReportSettingsPage.tsx`).
- Tests: `renderWithProviders(node, { route })` + wrap with `TenantContext.Provider` and/or `AuthContext.Provider`. Auth test value shape (from `src/components/__tests__/appshell.test.tsx`): `<AuthContext.Provider value={{ me: { id, email, name, is_superadmin }, loading: false, refresh: vi.fn(), setMe: vi.fn() }}>`. MSW URLs are RELATIVE (`/api/...`).

---

## Task 1: Regen API types + i18n + `useIsSuperadmin`

**Files:** Modify `frontend/openapi.json`, `frontend/src/api/schema.d.ts`, `frontend/src/i18n/en.ts`; Create `frontend/src/auth/useIsSuperadmin.ts`, `frontend/src/auth/__tests__/useIsSuperadmin.test.tsx`.

- [ ] **Step 1: Regenerate the typed client**

Run: `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm run gen:api`
Then verify the endpoints + schemas landed:
Run: `rg -n "/api/templates|TemplateOut|TemplateIn|OverrideIn|ApplyTemplateIn|TemplatePreviewOut" src/api/schema.d.ts | head`
Expected: the `/api/templates`, override, preview, apply paths + the schema names appear. If none, STOP and report (backend export failed).

- [ ] **Step 2: Add i18n strings** — in `frontend/src/i18n/en.ts`, add a `templates` block (sibling of `firmware`) and a `nav.templates` key. READ the file to match indentation + add `templates:` to the `nav` object:
```ts
    templates: "Template library",
```
New top-level block:
```ts
  templates: {
    tab: "Templates",
    libraryTitle: "Template library",
    superadminOnly: "The template library is managed by platform administrators.",
    name: "Name",
    kind: "Kind",
    type: "Alias type",
    content: "Content (one entry per line)",
    description: "Description",
    create: "New template",
    edit: "Edit",
    save: "Save",
    delete: "Delete",
    deleteConfirm: "Delete this template? Per-tenant overrides are removed; applied changes keep their history.",
    empty: "No templates yet.",
    created: "Template created",
    updated: "Template updated",
    saveFailed: "Could not save the template",
    apply: {
      title: "Apply a template",
      pick: "Template",
      override: "This customer's override (content, one entry per line — leave empty to use the library value)",
      saveOverride: "Save override",
      overrideSaved: "Override saved",
      preview: "Preview",
      previewTitle: "Effective configuration",
      applyConfirm: "Apply this template to the device?",
      runNow: "Apply now",
      scheduleAt: "Schedule (leave empty to apply now)",
      schedule: "Schedule",
      queued: "Apply queued",
      failed: "Could not apply the template",
      none: "No templates available.",
    },
  },
```

- [ ] **Step 3: Create `frontend/src/auth/useIsSuperadmin.ts`:**
```ts
import { useAuth } from "./useAuth";

/** True when the current user is a platform superadmin (manages the global template library). */
export function useIsSuperadmin(): boolean {
  return useAuth().me?.is_superadmin ?? false;
}
```

- [ ] **Step 4: Test `frontend/src/auth/__tests__/useIsSuperadmin.test.tsx`:**
```tsx
import { describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { AuthContext } from "../AuthProvider";
import { useIsSuperadmin } from "../useIsSuperadmin";

function wrap(is_superadmin: boolean) {
  return ({ children }: { children: ReactNode }) => (
    <AuthContext.Provider
      value={{
        me: { id: "1", email: "a@x.io", name: "A", is_superadmin },
        loading: false, refresh: vi.fn(), setMe: vi.fn(),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

describe("useIsSuperadmin", () => {
  it("is true for a superadmin", () => {
    expect(renderHook(() => useIsSuperadmin(), { wrapper: wrap(true) }).result.current).toBe(true);
  });
  it("is false otherwise", () => {
    expect(renderHook(() => useIsSuperadmin(), { wrapper: wrap(false) }).result.current).toBe(false);
  });
});
```

- [ ] **Step 5: Verify + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend
npx vitest run src/auth/__tests__/useIsSuperadmin.test.tsx
npx tsc --noEmit && npm run lint
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/openapi.json frontend/src/api/schema.d.ts frontend/src/i18n/en.ts frontend/src/auth/useIsSuperadmin.ts frontend/src/auth/__tests__/useIsSuperadmin.test.tsx
git commit -m "feat(fe): regen template API types + i18n + useIsSuperadmin gate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Library CRUD hooks

**Files:** Create `frontend/src/templates/hooks.ts`, `frontend/src/templates/__tests__/hooks.test.tsx`.

**Context:** The library is GLOBAL (no tenant in the path). Query key `["templates"]`. Mirror `src/firmware/hooks.ts` conventions.

- [ ] **Step 1: Write the test** `frontend/src/templates/__tests__/hooks.test.tsx`:
```tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { useCreateTemplate, useTemplates } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <I18nProvider><QueryClientProvider client={qc}>{children}</QueryClientProvider></I18nProvider>;
}

const T = { id: "x1", kind: "firewall_alias", name: "web", description: "", version: 1,
  body: { name: "web", type: "host", content: ["1.2.3.4"], description: "" },
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };

describe("template library hooks", () => {
  it("useTemplates lists the library", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const { result } = renderHook(() => useTemplates(), { wrapper });
    await waitFor(() => expect(result.current.data?.length).toBe(1));
    expect(result.current.data?.[0].name).toBe("web");
  });

  it("useCreateTemplate POSTs the body", async () => {
    let captured: unknown = null;
    server.use(http.post("/api/templates", async ({ request }) => {
      captured = await request.json();
      return HttpResponse.json(T, { status: 201 });
    }));
    const { result } = renderHook(() => useCreateTemplate(), { wrapper });
    await result.current.mutateAsync({ kind: "firewall_alias", name: "web", description: "",
      body: { name: "web", type: "host", content: ["1.2.3.4"], description: "" } });
    expect(captured).toMatchObject({ name: "web", kind: "firewall_alias" });
  });
});
```

- [ ] **Step 2: Run → FAIL** (`npx vitest run src/templates/__tests__/hooks.test.tsx`).

- [ ] **Step 3: Implement `frontend/src/templates/hooks.ts`:**
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import type { components } from "../api/schema";

export type Template = components["schemas"]["TemplateOut"];
export type TemplateIn = components["schemas"]["TemplateIn"];
export type TemplateUpdateIn = components["schemas"]["TemplateUpdateIn"];

export function useTemplates() {
  return useQuery({
    queryKey: ["templates"],
    queryFn: async (): Promise<Template[]> => {
      const { data, error } = await api.GET("/api/templates", {});
      if (error || !data) throw new Error("templates load failed");
      return data;
    },
  });
}

export function useCreateTemplate() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (body: TemplateIn): Promise<Template> => {
      const { data, error } = await api.POST("/api/templates", { body });
      if (error || !data) throw new Error(t.templates.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });
}

export function useUpdateTemplate() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: TemplateUpdateIn }): Promise<Template> => {
      const { data, error } = await api.PUT("/api/templates/{template_id}", {
        params: { path: { template_id: id } }, body });
      if (error || !data) throw new Error(t.templates.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/templates/{template_id}", {
        params: { path: { template_id: id } } });
      if (error) throw new Error(t.templates.saveFailed);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });
}
```
NOTE: confirm `api.GET("/api/templates", {})` is the correct openapi-fetch call form for a no-param GET (it may need `{}` or no second arg — check how an existing no-path-param GET is called, e.g. `/api/me` in `AuthProvider.tsx` uses `api.GET("/api/me")`). Match that. Confirm the generated `components["schemas"]` names from Task 1.

- [ ] **Step 4: Run → PASS.** Commit:
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/templates/hooks.ts frontend/src/templates/__tests__/hooks.test.tsx
git commit -m "feat(fe): template library CRUD hooks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Template Library page (superadmin CRUD)

**Files:** Create `frontend/src/pages/TemplateLibraryPage.tsx`, `frontend/src/templates/TemplateFormModal.tsx`, `frontend/src/pages/__tests__/templateLibrary.test.tsx`.

**Context:** Superadmin-gated page. List templates in a `Table`; "New template" + per-row Edit/Delete. The form modal edits name + alias type (`Select`) + content (`Textarea`, one entry per line) + description. On submit: split content on `\n`, trim, filter empties → `body.content: string[]`, and set `body.name`/`body.type` from the form (the backend pins them, but create needs them). Mirror `DeviceCreateModal` (useForm + mutation) and `ProposeAliasModal` (content textarea). Delete via `ConfirmModal`.

- [ ] **Step 1: Write the test** `frontend/src/pages/__tests__/templateLibrary.test.tsx` (superadmin sees CRUD; create POSTs a parsed body; non-superadmin sees the gate):
```tsx
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { AuthContext } from "../../auth/AuthProvider";
import { renderWithProviders } from "../../test/utils";
import { TemplateLibraryPage } from "../TemplateLibraryPage";

function withAuth(node: ReactNode, is_superadmin: boolean) {
  return (
    <AuthContext.Provider value={{
      me: { id: "1", email: "a@x.io", name: "A", is_superadmin },
      loading: false, refresh: vi.fn(), setMe: vi.fn() }}>
      {node}
    </AuthContext.Provider>
  );
}
const T = { id: "x1", kind: "firewall_alias", name: "web", description: "d", version: 1,
  body: { name: "web", type: "host", content: ["1.2.3.4"], description: "d" },
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };

describe("TemplateLibraryPage", () => {
  it("shows the superadmin-only gate for non-superadmins", () => {
    renderWithProviders(withAuth(<TemplateLibraryPage />, false));
    expect(screen.getByTestId("tpl-superadmin-gate")).toBeInTheDocument();
    expect(screen.queryByTestId("tpl-new")).toBeNull();
  });

  it("lists templates and creates one (content parsed to a list)", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const posted = vi.fn();
    server.use(http.post("/api/templates", async ({ request }) => {
      posted(await request.json());
      return HttpResponse.json(T, { status: 201 });
    }));
    renderWithProviders(withAuth(<TemplateLibraryPage />, true));
    expect(await screen.findByText("web")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("tpl-new"));
    await userEvent.type(screen.getByTestId("tpl-name"), "db");
    await userEvent.type(screen.getByTestId("tpl-content"), "10.0.0.1\n10.0.0.2");
    await userEvent.click(screen.getByTestId("tpl-save"));
    await waitFor(() => expect(posted).toHaveBeenCalled());
    const body = posted.mock.calls[0][0];
    expect(body.name).toBe("db");
    expect(body.body.content).toEqual(["10.0.0.1", "10.0.0.2"]);  // newlines -> list
  });
});
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `frontend/src/templates/TemplateFormModal.tsx`:**
```tsx
import { Button, Group, Modal, Select, Stack, Textarea, TextInput } from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";
import { useEffect } from "react";
import { useT } from "../i18n";
import { type Template, useCreateTemplate, useUpdateTemplate } from "./hooks";

const ALIAS_TYPES = ["host", "network", "port", "url", "urltable", "geoip", "networkgroup", "mac", "dynipv6host"];

export function TemplateFormModal(
  { opened, onClose, editing }: { opened: boolean; onClose: () => void; editing: Template | null },
) {
  const t = useT();
  const create = useCreateTemplate();
  const update = useUpdateTemplate();
  const form = useForm({
    initialValues: { name: "", type: "host", content: "", description: "" },
  });

  useEffect(() => {
    if (opened) {
      form.setValues(editing
        ? { name: editing.name, type: String(editing.body?.type ?? "host"),
            content: (Array.isArray(editing.body?.content) ? editing.body.content : []).join("\n"),
            description: editing.description ?? "" }
        : { name: "", type: "host", content: "", description: "" });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opened, editing]);

  async function submit(v: typeof form.values) {
    const content = v.content.split("\n").map((s) => s.trim()).filter(Boolean);
    const body = { name: v.name, type: v.type, content, description: v.description };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, body: { name: v.name, description: v.description, body } });
        notifications.show({ message: t.templates.updated });
      } else {
        await create.mutateAsync({ kind: "firewall_alias", name: v.name, description: v.description, body });
        notifications.show({ message: t.templates.created });
      }
      onClose();
    } catch {
      notifications.show({ color: "red", message: t.templates.saveFailed });
    }
  }

  return (
    <Modal opened={opened} onClose={onClose} title={editing ? t.templates.edit : t.templates.create}
           transitionProps={{ duration: 0 }} data-testid="tpl-modal">
      <form onSubmit={form.onSubmit(submit)}>
        <Stack>
          <TextInput label={t.templates.name} required data-testid="tpl-name" {...form.getInputProps("name")} />
          <Select label={t.templates.type} data={ALIAS_TYPES} data-testid="tpl-type" {...form.getInputProps("type")} />
          <Textarea label={t.templates.content} rows={4} required data-testid="tpl-content"
                    {...form.getInputProps("content")} />
          <TextInput label={t.templates.description} data-testid="tpl-desc" {...form.getInputProps("description")} />
          <Group justify="flex-end">
            <Button type="submit" loading={create.isPending || update.isPending} data-testid="tpl-save">
              {t.templates.save}
            </Button>
          </Group>
        </Stack>
      </form>
    </Modal>
  );
}
```

- [ ] **Step 4: Implement `frontend/src/pages/TemplateLibraryPage.tsx`:**
```tsx
import { Alert, Badge, Button, Group, Stack, Table, Text, Title } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useState } from "react";
import { ConfirmModal } from "../components/ConfirmModal";
import { useIsSuperadmin } from "../auth/useIsSuperadmin";
import { useT } from "../i18n";
import { type Template, useDeleteTemplate, useTemplates } from "../templates/hooks";
import { TemplateFormModal } from "../templates/TemplateFormModal";

export function TemplateLibraryPage() {
  const t = useT();
  const isSuper = useIsSuperadmin();
  const { data: templates } = useTemplates();
  const del = useDeleteTemplate();
  const [modalOpen, setModalOpen] = useState(false);
  const [editing, setEditing] = useState<Template | null>(null);
  const [toDelete, setToDelete] = useState<Template | null>(null);

  if (!isSuper) {
    return <Alert color="yellow" data-testid="tpl-superadmin-gate">{t.templates.superadminOnly}</Alert>;
  }

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>{t.templates.libraryTitle}</Title>
        <Button data-testid="tpl-new" onClick={() => { setEditing(null); setModalOpen(true); }}>
          {t.templates.create}
        </Button>
      </Group>
      {templates && templates.length > 0 ? (
        <Table>
          <Table.Thead><Table.Tr>
            <Table.Th>{t.templates.name}</Table.Th><Table.Th>{t.templates.kind}</Table.Th>
            <Table.Th>{t.templates.description}</Table.Th><Table.Th /></Table.Tr></Table.Thead>
          <Table.Tbody>
            {templates.map((tpl) => (
              <Table.Tr key={tpl.id}>
                <Table.Td>{tpl.name}</Table.Td>
                <Table.Td><Badge variant="light">{tpl.kind}</Badge></Table.Td>
                <Table.Td>{tpl.description}</Table.Td>
                <Table.Td>
                  <Group gap="xs" justify="flex-end">
                    <Button size="xs" variant="light" onClick={() => { setEditing(tpl); setModalOpen(true); }}>
                      {t.templates.edit}
                    </Button>
                    <Button size="xs" variant="light" color="red" onClick={() => setToDelete(tpl)}>
                      {t.templates.delete}
                    </Button>
                  </Group>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      ) : <Text c="dimmed">{t.templates.empty}</Text>}

      <TemplateFormModal opened={modalOpen} onClose={() => setModalOpen(false)} editing={editing} />
      <ConfirmModal
        opened={!!toDelete}
        onClose={() => setToDelete(null)}
        onConfirm={async () => {
          const tpl = toDelete; setToDelete(null);
          if (!tpl) return;
          try { await del.mutateAsync(tpl.id); } catch { notifications.show({ color: "red", message: t.templates.saveFailed }); }
        }}
        title={t.templates.delete}
        body={t.templates.deleteConfirm}
        loading={del.isPending}
      />
    </Stack>
  );
}
```

- [ ] **Step 5: Run the test → PASS; `npx tsc --noEmit && npm run lint`; commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/pages/TemplateLibraryPage.tsx frontend/src/templates/TemplateFormModal.tsx frontend/src/pages/__tests__/templateLibrary.test.tsx
git commit -m "feat(fe): Template Library page (superadmin CRUD, content-as-textarea)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Route + nav wiring

**Files:** Modify `frontend/src/components/AppShell.tsx`; Create/extend `frontend/src/components/__tests__/appshell.test.tsx` (it exists — ADD cases, don't overwrite).

**Context:** Add a `/admin/templates` route and a superadmin-only nav link. READ `AppShell.tsx`: it lazy-loads pages, defines `<Route>`s, and `AppShellNav()` renders `NavLink`s with the `{role === "tenant_admin" && ...}` gate. Add the superadmin gate using `useAuth()`/`useIsSuperadmin()`.

- [ ] **Step 1: Add a failing nav test** to `frontend/src/components/__tests__/appshell.test.tsx` — READ the file first to reuse its existing auth/render harness. Add:
```tsx
  it("shows the Template library nav link only for superadmins", () => {
    // render the nav with a superadmin me -> link present; with non-superadmin -> absent.
    // (Mirror the file's existing render helper; assert on the link text/role for /admin/templates.)
  });
```
Flesh it out using the file's existing pattern (it already builds an `AuthContext.Provider` with a `me`). Assert the link to `/admin/templates` appears when `me.is_superadmin` is true and is absent when false.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement in `AppShell.tsx`:**
  - Lazy-import the page next to the others: `const TemplateLibraryPage = lazy(() => import("../pages/TemplateLibraryPage").then((m) => ({ default: m.TemplateLibraryPage })));` (match the file's exact lazy-import style).
  - Add the route next to the others: `<Route path="/admin/templates" element={<TemplateLibraryPage />} />`.
  - In `AppShellNav()`, add (using `useAuth()` — import it; or `useIsSuperadmin()`):
    ```tsx
    {me?.is_superadmin && (
      <NavLink component={RouterNavLink} to="/admin/templates" label={t.nav.templates} />
    )}
    ```
    (Get `me` via `const { me } = useAuth();` at the top of `AppShellNav`, mirroring how `role` is obtained.)

- [ ] **Step 4: Run → PASS; full suite + lint + build:**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test && npm run lint && npm run build
```
Expected: all green (existing appshell tests still pass).

- [ ] **Step 5: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/components/AppShell.tsx frontend/src/components/__tests__/appshell.test.tsx
git commit -m "feat(fe): Template Library route + superadmin nav link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Apply-template flow on the device

**Files:** Create `frontend/src/templates/applyHooks.ts`, `frontend/src/templates/ApplyTemplateTab.tsx`, `frontend/src/templates/__tests__/applyTemplate.test.tsx`; Modify `frontend/src/pages/DeviceDetailPage.tsx`.

**Context:** A new "Templates" tab on the device. Flow: pick a library template (from `useTemplates`) → optionally edit this tenant's override (a content textarea → `body_patch.content`) and Save it (`PUT .../override`) → Preview (`POST .../preview` → show `TemplatePreviewOut.new`) → Apply behind the FirmwareActions-style confirm+schedule modal (`POST .../apply`, now or `scheduled_at`). After apply, invalidate the device's config changes.

- [ ] **Step 1: Create `frontend/src/templates/applyHooks.ts`:**
```ts
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type TemplatePreview = components["schemas"]["TemplatePreviewOut"];

export function useUpsertOverride(templateId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (body_patch: Record<string, unknown>) => {
      const { data, error } = await api.PUT(
        "/api/tenants/{tenant_id}/templates/{template_id}/override",
        { params: { path: { tenant_id: activeId!, template_id: templateId } }, body: { body_patch } });
      if (error || !data) throw new Error(t.templates.apply.failed);
      return data;
    },
  });
}

export function usePreviewTemplate(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (templateId: string): Promise<TemplatePreview> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/preview",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, template_id: templateId } } });
      if (error || !data) throw new Error(t.templates.apply.failed);
      return data;
    },
  });
}

export function useApplyTemplate(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({ templateId, scheduled_at }: { templateId: string; scheduled_at: string | null }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/apply",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, template_id: templateId } },
          body: { scheduled_at } });
      if (error || !data) throw new Error(t.templates.apply.failed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-changes", activeId, deviceId] }),
  });
}
```
NOTE: confirm the preview/apply POSTs don't need a `body` when there's none (preview takes no body; apply takes `{scheduled_at}`). If openapi-fetch's types require `body` for preview, pass `body: undefined`. Confirm the `["config-changes", activeId, deviceId]` key matches `src/config/changeHooks.ts`.

- [ ] **Step 2: Write the test** `frontend/src/templates/__tests__/applyTemplate.test.tsx` (pick → preview shows effective content → apply now POSTs scheduled_at null). Use `renderWithProviders(withTenant(<ApplyTemplateTab deviceId="d1" />))` with the `withTenant` helper (TenantContext with activeId "t1"); MSW relative URLs `/api/templates` (list) + `/api/tenants/t1/devices/d1/templates/{id}/preview|apply`. Mirror the `FirmwareActions` test structure (confirm modal → `btn-tpl-apply-now`). Assert the apply POST body `{scheduled_at: null}` and that the preview shows the effective content. (Write it fully following `src/firmware/__tests__/firmwareActions.test.tsx`.)

- [ ] **Step 3: Run → FAIL.**

- [ ] **Step 4: Implement `frontend/src/templates/ApplyTemplateTab.tsx`** — a `Select` of templates (label = name) → on pick, show an override `Textarea` (prefilled from the chosen template's content; Save → `useUpsertOverride`) + a "Preview" button (→ `usePreviewTemplate`, render `preview.new.name` + `preview.new.content` joined) + an "Apply" button opening a confirm+schedule `Modal` (reuse the `FirmwareActions` modal shape: a description, a `DateTimePicker` "leave empty to apply now", `Apply now` / `Schedule` buttons). On apply, call `useApplyTemplate` with `scheduled_at = scheduled ? new Date(when.replace(" ", "T")).toISOString() : null` (ISO conversion like `FirmwareActions`/`ChangesPanel`). `data-testid`s: `tpl-pick`, `tpl-override`, `tpl-override-save`, `tpl-preview`, `tpl-preview-out`, `btn-tpl-apply`, `tpl-confirm-modal`, `btn-tpl-apply-now`, `btn-tpl-apply-schedule`, `tpl-schedule-picker`. Use `useT()`, `notifications`. Read `src/firmware/FirmwareActions.tsx` and mirror its modal + ISO-conversion logic exactly.

- [ ] **Step 5: Wire the tab** in `frontend/src/pages/DeviceDetailPage.tsx` — add `import { ApplyTemplateTab } from "../templates/ApplyTemplateTab";`, a `<Tabs.Tab value="templates">{t.templates.tab}</Tabs.Tab>` after the firmware tab, and a `<Tabs.Panel value="templates" pt="md">{deviceId && <ApplyTemplateTab deviceId={deviceId} />}</Tabs.Panel>` after the firmware panel.

- [ ] **Step 6: Run the test → PASS; full suite + lint + build:**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test && npm run lint && npm run build
```
Expected: all green (the existing `devicedetail.test.tsx` must still pass — the new tab mounts lazily on activation, so it won't fire the templates list on the Info tab; if `DeviceDetailPage` eagerly mounts panels, add a `http.get("/api/templates", ...)` handler to those tests — but Mantine tabs mount lazily here, confirmed for firmware).

- [ ] **Step 7: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/templates/applyHooks.ts frontend/src/templates/ApplyTemplateTab.tsx frontend/src/templates/__tests__/applyTemplate.test.tsx frontend/src/pages/DeviceDetailPage.tsx
git commit -m "feat(fe): apply-template tab on the device (pick/override/preview/apply now-scheduled)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full frontend suite green: `cd frontend && npm test`
- [ ] Lint clean: `npm run lint`; Build: `npm run build`
- [ ] Final holistic review, then superpowers:finishing-a-development-branch → PR to protected `main`.
- [ ] After merge: update `README.md` (config-templates M1 shipped, engine + UI) per the keep-README-updated convention.

---

## Self-Review (author)

**Spec coverage (UI, §4.5):** superadmin Template Library CRUD (Tasks 2-4) gated by the new `useIsSuperadmin` (Task 1); the per-device apply flow — pick → per-tenant override → redacted preview → apply now/scheduled reusing the device-actions confirm+schedule modal (Task 5). `content: string[]` edited as a newline textarea (alias convention). Nav/route gated to superadmins (Task 4).

**Placeholder scan:** Tasks 1-3 carry complete code; Tasks 4-5's component bodies are specified with exact testids + the concrete files to mirror (`AppShell.tsx`/`appshell.test.tsx`, `FirmwareActions.tsx`) and the ISO-conversion rule — the implementer fleshes the two UI components against those exact patterns, not a vague TODO.

**Type consistency:** `Template`/`TemplateIn`/`TemplateUpdateIn` (Task 2) and `TemplatePreview` (Task 5) come from the regenerated `components["schemas"]` (Task 1); query key `["templates"]` is shared by the list query + all CRUD invalidations; apply invalidates `["config-changes", activeId, deviceId]` (matches config-push); the apply `scheduled_at` ISO-conversion matches `FirmwareActions`; testids used in tests exist in the components.
