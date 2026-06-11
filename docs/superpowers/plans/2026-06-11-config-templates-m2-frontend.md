# Configuration Templates — M2 (Profiles) Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** UI for profiles (M2): manage profiles in the superadmin library (name + description + an **ordered** set of templates) and **apply a profile to a device** in one shot (pick → preview the ordered member set → apply now/scheduled). Heavily reuses the M1 templates UI.

**Architecture:** Regenerate the typed client for `/api/profiles*`; profile CRUD + apply hooks (mirror `src/templates/hooks.ts`/`applyHooks.ts`); a **Profiles** tab on the superadmin Library page (a profile form = name + description + an ordered template `MultiSelect`); an **Apply a profile** section on the device's Templates tab (mirror `ApplyTemplateTab`, reusing the confirm+schedule modal pattern).

**Tech Stack:** Vite + React 19 + Mantine v9 + TanStack Query v5 + typed openapi-fetch + Vitest/RTL/MSW. English via `useT()`.

**Spec:** `docs/superpowers/specs/2026-06-11-config-templates-m2-profiles-design.md`
**Branch:** `feat/config-templates-m2-frontend` (created).
**Backend (merged):** `GET /api/profiles` (any auth), `POST /api/profiles` (201, superadmin, body `{name, description, template_ids: uuid[]}`), `PUT /api/profiles/{id}` (superadmin, optional `{name?, description?, template_ids?}` — `template_ids` replaces the ordered set), `DELETE /api/profiles/{id}` (204); `POST /api/tenants/{tid}/devices/{did}/profiles/{id}/preview` → `TemplatePreviewOut[]` (ordered); `POST .../profiles/{id}/apply` → `{change_ids, status}`. `ProfileOut` has `template_ids: uuid[]` (ordered).

**Run:** `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test`; `npm run lint`; `npm run build`. English; commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Conventions (reuse from M1)
- `src/templates/hooks.ts` (library CRUD) and `src/templates/applyHooks.ts` (preview/apply) are the exact models for the profile hooks. `src/templates/TemplateFormModal.tsx` + `src/pages/TemplateLibraryPage.tsx` (superadmin CRUD page) and `src/templates/ApplyTemplateTab.tsx` (pick → preview → confirm+schedule apply) are the component models. `useIsSuperadmin` gates the library. Tests: `renderWithProviders` + `AuthContext.Provider` (superadmin) / `TenantContext.Provider`; MSW relative URLs.
- `useTemplates()` (M1) gives the template list for the profile's ordered `MultiSelect`.

---

## Task 1: Regen types + i18n

**Files:** Modify `frontend/openapi.json`, `frontend/src/api/schema.d.ts`, `frontend/src/i18n/en.ts`.

- [ ] **Step 1: Regen** — `cd frontend && npm run gen:api` (may already be applied). Verify: `rg -n "/api/profiles|ProfileOut|ProfileIn|ApplyProfileIn" src/api/schema.d.ts | head` — the paths + schemas appear. If none, STOP.
- [ ] **Step 2: i18n** — in `frontend/src/i18n/en.ts`, EXTEND the existing `templates` block with a `profiles` sub-block (add it inside `templates: { ... }`, e.g. after `apply`):
```ts
    profiles: {
      tab: "Profiles",
      title: "Profiles",
      name: "Name",
      description: "Description",
      members: "Templates (in apply order)",
      create: "New profile",
      edit: "Edit",
      save: "Save",
      delete: "Delete",
      deleteConfirm: "Delete this profile? Applied changes keep their history.",
      empty: "No profiles yet.",
      created: "Profile created",
      updated: "Profile updated",
      saveFailed: "Could not save the profile",
      memberCount: "templates",
      apply: {
        title: "Apply a profile",
        pick: "Profile",
        preview: "Preview",
        previewTitle: "Effective configuration (per template, in order)",
        empty: "No profiles available.",
        applyConfirm: "Apply this profile to the device? It applies each member template in order.",
        runNow: "Apply now",
        scheduleAt: "Schedule (leave empty to apply now)",
        schedule: "Schedule",
        queued: "Profile apply queued",
        failed: "Could not apply the profile",
      },
    },
```
Also add a library-tab label inside the existing `templates` block if not present: `templatesTab: "Templates"` (used by the Library page tabs).
- [ ] **Step 3: typecheck + commit**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npx tsc --noEmit && npm run lint
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/openapi.json frontend/src/api/schema.d.ts frontend/src/i18n/en.ts
git commit -m "feat(fe): regen profile API types + i18n

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Profile hooks (CRUD + preview/apply)

**Files:** Create `frontend/src/profiles/hooks.ts`, `frontend/src/profiles/__tests__/hooks.test.tsx`.

**Context:** Mirror `src/templates/hooks.ts` (global library, key `["profiles"]`) + `src/templates/applyHooks.ts` (tenant preview/apply). 

- [ ] **Step 1: Write the test** (mirror `src/templates/__tests__/hooks.test.tsx`): `useProfiles` lists `/api/profiles`; `useCreateProfile` POSTs `{name, description, template_ids}`; `useApplyProfile(deviceId)` POSTs `.../profiles/{id}/apply` with `{scheduled_at}`. Wrapper = `I18nProvider` + `QueryClientProvider` + `TenantContext.Provider` (activeId "t1") for the apply hook. MSW relative URLs.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `frontend/src/profiles/hooks.ts`:**
```ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type Profile = components["schemas"]["ProfileOut"];
export type ProfileIn = components["schemas"]["ProfileIn"];
export type ProfileUpdateIn = components["schemas"]["ProfileUpdateIn"];
export type TemplatePreview = components["schemas"]["TemplatePreviewOut"];

export function useProfiles() {
  return useQuery({
    queryKey: ["profiles"],
    queryFn: async (): Promise<Profile[]> => {
      const { data, error } = await api.GET("/api/profiles");
      if (error || !data) throw new Error("profiles load failed");
      return data;
    },
  });
}

export function useCreateProfile() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (body: ProfileIn): Promise<Profile> => {
      const { data, error } = await api.POST("/api/profiles", { body });
      if (error || !data) throw new Error(t.templates.profiles.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: ProfileUpdateIn }): Promise<Profile> => {
      const { data, error } = await api.PUT("/api/profiles/{profile_id}", {
        params: { path: { profile_id: id } }, body });
      if (error || !data) throw new Error(t.templates.profiles.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });
}

export function useDeleteProfile() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/profiles/{profile_id}", {
        params: { path: { profile_id: id } } });
      if (error) throw new Error(t.templates.profiles.saveFailed);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });
}

export function usePreviewProfile(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (profileId: string): Promise<TemplatePreview[]> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/preview",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, profile_id: profileId } } });
      if (error || !data) throw new Error(t.templates.profiles.apply.failed);
      return data;
    },
  });
}

export function useApplyProfile(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({ profileId, scheduled_at }: { profileId: string; scheduled_at: string | null }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/apply",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, profile_id: profileId } },
          body: { scheduled_at } });
      if (error || !data) throw new Error(t.templates.profiles.apply.failed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-changes", activeId, deviceId] }),
  });
}
```
NOTE: confirm `api.GET("/api/profiles")` (no-arg) form (matches `useTemplates`); confirm the generated `components["schemas"]` names.
- [ ] **Step 4: Run → PASS; commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/profiles/hooks.ts frontend/src/profiles/__tests__/hooks.test.tsx
git commit -m "feat(fe): profile hooks (CRUD + preview/apply)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Profiles management UI (library Profiles tab)

**Files:** Create `frontend/src/profiles/ProfileFormModal.tsx`, `frontend/src/profiles/ProfilesPanel.tsx`, `frontend/src/profiles/__tests__/profilesPanel.test.tsx`; Modify `frontend/src/pages/TemplateLibraryPage.tsx`.

**Context:** Add a **Profiles** tab to the superadmin Library page next to the existing Templates table. The profile form = name + description + an **ordered** template `MultiSelect` (Mantine `MultiSelect`'s `value` array preserves selection order → that IS the member order). Mirror `TemplateFormModal`/`TemplateLibraryPage` (M1).

- [ ] **Step 1: Wrap the Library page in Tabs.** In `frontend/src/pages/TemplateLibraryPage.tsx`, keep the superadmin gate, then render Mantine `Tabs` with `defaultValue="templates"`: a "Templates" tab containing the EXISTING templates table + modal (move the current body there unchanged), and a "Profiles" tab rendering `<ProfilesPanel />`. The existing `templateLibrary.test.tsx` must still pass (the Templates tab is default, so its content renders). Use `t.templates.templatesTab` / `t.templates.profiles.tab` for the tab labels. Keep all existing data-testids on the templates side.

- [ ] **Step 2: Write `frontend/src/profiles/__tests__/profilesPanel.test.tsx`** — render `withAuth(<ProfilesPanel />, true)` (superadmin AuthContext, mirror `templateLibrary.test.tsx`'s `withAuth`). MSW: `GET /api/templates` (two templates, for the MultiSelect options), `GET /api/profiles` (one profile), `POST /api/profiles` (capture body, return 201). Tests: lists a profile; "New profile" → fill name + pick two templates in the MultiSelect → save → POST body has `name` + `template_ids` (the two ids in pick order). (Driving Mantine `MultiSelect` in jsdom: type to open + click options, or set the form value directly — if flaky, assert the POST `template_ids` after selecting via the testid'd input; mirror however `TemplateFormModal`'s `Select` is driven in `templateLibrary.test.tsx` — `Select` worked there, `MultiSelect` is similar but if it's flaky, use a controlled approach.)

- [ ] **Step 3: Implement `frontend/src/profiles/ProfileFormModal.tsx`** — mirror `TemplateFormModal`: Mantine `useForm` with `{ name, description, template_ids: string[] }`; a `TextInput` (name, testid `prof-name`), `TextInput` (description), and a Mantine `MultiSelect` (testid `prof-members`) with `data = templates.map(t => ({ value: t.id, label: t.name }))` from `useTemplates()`, `value`/`onChange` bound to `template_ids` (selection order = member order). On submit: create → `useCreateProfile().mutateAsync({ name, description, template_ids })`; edit → `useUpdateProfile().mutateAsync({ id, body: { name, description, template_ids } })`. `notifications` on success/failure. Prefill on edit from `editing.template_ids`. Save button testid `prof-save`.

- [ ] **Step 4: Implement `frontend/src/profiles/ProfilesPanel.tsx`** — mirror `TemplateLibraryPage`'s table body: `useProfiles()` → a `Table` (name, description, member count = `template_ids.length`) with Edit/Delete per row; "New profile" button (testid `prof-new`); delete via `ConfirmModal`; the `ProfileFormModal`. (No superadmin gate here — the parent Library page already gates; but it's fine to render inside the gated page.)

- [ ] **Step 5: Run the tests → PASS; `npx tsc --noEmit && npm run lint`; commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/profiles/ProfileFormModal.tsx frontend/src/profiles/ProfilesPanel.tsx frontend/src/profiles/__tests__/profilesPanel.test.tsx frontend/src/pages/TemplateLibraryPage.tsx
git commit -m "feat(fe): Profiles management tab in the library (ordered template members)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Apply-profile on the device

**Files:** Create `frontend/src/profiles/ApplyProfileSection.tsx`, `frontend/src/profiles/__tests__/applyProfile.test.tsx`; Modify `frontend/src/pages/DeviceDetailPage.tsx`.

**Context:** Add an "Apply a profile" section to the device's **Templates** tab (below the existing `ApplyTemplateTab`). Mirror `ApplyTemplateTab` exactly: a `Select` of profiles → "Preview" (`usePreviewProfile` → render the ordered list of member previews) → "Apply" opening a confirm+schedule `Modal` (description + `DateTimePicker` "leave empty to apply now" + Apply now / Schedule), firing `useApplyProfile` with `scheduled_at = scheduled ? new Date(when.replace(" ", "T")).toISOString() : null`. data-testids: `prof-pick`, `prof-preview`, `prof-preview-out`, `btn-prof-apply`, `prof-confirm-modal`, `btn-prof-apply-now`, `btn-prof-apply-schedule`, `prof-schedule-picker`. Use the `vi.mock("@mantine/dates", ...)` deterministic picker in the schedule test (as in `applyTemplate.test.tsx`).

- [ ] **Step 1: Write `frontend/src/profiles/__tests__/applyProfile.test.tsx`** — `renderWithProviders(withTenant(<ApplyProfileSection deviceId="d1" />))`; MSW: `GET /api/profiles` (one profile), `POST /api/tenants/t1/devices/d1/profiles/{id}/preview` (a list of two `TemplatePreviewOut`), `POST .../apply` (capture body → `{change_ids:[...], status:"scheduled"}`). Tests: pick a profile → Preview shows the two member previews (in order); Apply now → POST body `{scheduled_at: null}`. Mirror `applyTemplate.test.tsx` exactly.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement `frontend/src/profiles/ApplyProfileSection.tsx`** — copy the structure of `ApplyTemplateTab.tsx` but for profiles: `useProfiles()` for the `Select`; `usePreviewProfile`/`useApplyProfile`; the preview renders the ordered member list (`preview.map((p) => p.new.name + ": " + (p.new.content as string[]).join(", "))` — cast `p.new` to `{name?:string; content?:string[]}`); the confirm+schedule modal mirrors `ApplyTemplateTab`'s. Use `t.templates.profiles.apply.*`.
- [ ] **Step 4: Wire into the device** — in `frontend/src/pages/DeviceDetailPage.tsx`, in the existing `templates` `Tabs.Panel`, render `<ApplyProfileSection deviceId={deviceId} />` AFTER `<ApplyTemplateTab deviceId={deviceId} />` (both in the same panel, e.g. wrap in a `<Stack>`). Add the import.
- [ ] **Step 5: Run the test → full suite + lint + build:**
```bash
cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test 2>&1 | tail -6 && npm run lint && npm run build 2>&1 | tail -3
```
Expected: all green (existing `devicedetail.test.tsx` still passes — the Templates tab now also mounts `ApplyProfileSection`, which fetches `/api/profiles` ONLY when the Templates tab is active; the existing devicedetail tests don't open the Templates tab, so no new handler needed — but CONFIRM: if `devicedetail.test.tsx` opens the Templates tab anywhere, add a `GET /api/profiles` + `GET /api/templates` handler. The M1 frontend confirmed lazy tab mounting).
- [ ] **Step 6: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/profiles/ApplyProfileSection.tsx frontend/src/profiles/__tests__/applyProfile.test.tsx frontend/src/pages/DeviceDetailPage.tsx
git commit -m "feat(fe): apply-profile section on the device (pick/preview/apply now-scheduled)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full frontend suite green: `cd frontend && npm test`; lint clean; build green.
- [ ] Final holistic review, then superpowers:finishing-a-development-branch → PR.
- [ ] After merge: update `README.md` (templates M2 profiles shipped).

---

## Self-Review (author)

**Spec coverage (UI):** profile library CRUD with an ordered template `MultiSelect` (Task 3, superadmin via the gated Library page's new Profiles tab); per-device apply-profile — pick → ordered preview → apply now/scheduled (Task 4), reusing the M1 apply modal + ISO conversion. Hooks mirror M1 (Task 2).

**Placeholder scan:** Tasks 1-2 carry complete code; Tasks 3-4 specify the components by mirroring the exact M1 files (`TemplateFormModal`/`TemplateLibraryPage`/`ApplyTemplateTab`) with concrete data-testids + the MultiSelect-order + ISO-conversion rules — not vague TODOs.

**Type consistency:** `Profile`/`ProfileIn`/`ProfileUpdateIn`/`TemplatePreview` from the regenerated `components["schemas"]` (Task 1); query key `["profiles"]` shared by the list + CRUD invalidations; apply invalidates `["config-changes", activeId, deviceId]` (matches M1); the apply `scheduled_at` ISO conversion matches `ApplyTemplateTab`; `ProfileOut.template_ids` (ordered) drives both the form prefill and the member-count column.
