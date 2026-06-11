# Configuration Templates — M3 generic `opnsense_setting` — Frontend Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The user-facing "create your own template for an OPNsense setting, with value control" UI: in the superadmin Template form, a new kind **OPNsense setting** → pick a catalog endpoint + a **reference device** → introspect → an **auto-generated, value-controlled form** (Switch / Select / MultiSelect / Text inferred from the device's `get`) → save `{endpoint_key, payload}`. Apply/preview reuse the existing template/profile UI (the backend preview is now kind-aware).

**Architecture:** Regenerate the typed client (`/api/opnsense/setting-endpoints`, the per-device introspection endpoint). Add hooks: `useSettingEndpoints` (catalog), `useIntrospectSetting(deviceId)` (mutation → field schema), `useTenantDevices()` (the active tenant's devices, for the reference-device picker). Add a `kind` selector to `TemplateFormModal` and an `OpnsenseSettingForm` sub-component that renders the auto-form from the field schema. The reference device is picked from the **active tenant's** devices (reuses `useTenant`), keeping the picker simple.

**Tech Stack:** Vite + React 19 + Mantine v9 + TanStack Query v5 + typed openapi-fetch + Vitest/RTL/MSW.

**Spec:** `docs/superpowers/specs/2026-06-11-config-templates-m3-generic-setting-design.md`
**Branch:** `feat/templates-m3-generic-frontend` (created).
**Backend (merged):** `GET /api/opnsense/setting-endpoints` → `[{key,label}]`; `GET /api/tenants/{tid}/devices/{did}/opnsense/settings/{endpoint_key}` → `{endpoint_key, label, fields: [{path, label, control, options?, value}]}` where `control ∈ {select, multiselect, switch, text}`. Template body for the kind: `{endpoint_key, payload: {<path>: <value>}}` (switch → "0"/"1"; select → the selected key; multiselect → comma-joined keys; text → string). The kind value is `"opnsense_setting"`.

**Run:** `cd /home/l0rdg3x/coding/OPNGMS/frontend && npm test`; `npm run lint`; `npm run build`. English via `useT()`. Trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: i18n strings (types already regenerated)

**Files:** Modify `frontend/openapi.json` + `frontend/src/api/schema.d.ts` (already regenerated — commit them), `frontend/src/i18n/en.ts`.

- [ ] **Step 1:** confirm the regenerated schema has the endpoints: `rg -n "opnsense/setting-endpoints|opnsense/settings" frontend/src/api/schema.d.ts`. (Already run; if missing, `cd frontend && npm run gen:api`.)
- [ ] **Step 2:** in `frontend/src/i18n/en.ts`, EXTEND the `templates` block with a `setting` sub-block (sibling of `profiles`/`apply`):
```ts
    kindLabel: "Template type",
    kindAlias: "Firewall alias",
    kindSetting: "OPNsense setting",
    setting: {
      endpoint: "Setting",
      referenceDevice: "Reference device (to read the available fields)",
      load: "Load fields",
      loadHint: "Pick a setting + a device, then load the fields to configure.",
      noDevice: "No device available in the active tenant to read fields from.",
      loadFailed: "Could not read the setting from the device.",
      noFields: "No configurable fields.",
      hardwareNote: "Hardware/device-specific fields are intentionally not templatable.",
    },
```
(Adjust placement; keep TS valid.)
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit && npm run lint`; commit:
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/openapi.json frontend/src/api/schema.d.ts frontend/src/i18n/en.ts
git commit -m "feat(fe): regen setting API types + i18n

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: hooks (catalog, introspection, tenant devices)

**Files:** Create `frontend/src/templates/settingHooks.ts`, `frontend/src/templates/__tests__/settingHooks.test.tsx`.

**Context:** Mirror `src/templates/hooks.ts`/`applyHooks.ts`. The catalog is global (`["setting-endpoints"]`); introspection is a mutation (POST-like GET driven by user action); tenant devices reuse `useTenant().activeId`.

- [ ] **Step 1: Write the test** (`settingHooks.test.tsx`) — `useSettingEndpoints` lists `/api/opnsense/setting-endpoints`; `useIntrospectSetting("d1").mutateAsync("ids_general")` GETs `/api/tenants/t1/devices/d1/opnsense/settings/ids_general` and returns the `fields`. Wrapper: `I18nProvider` + `QueryClientProvider` + `TenantContext.Provider` (activeId "t1"). MSW relative URLs. Run → FAIL.
- [ ] **Step 2: Implement `frontend/src/templates/settingHooks.ts`:**
```ts
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

export type SettingField = {
  path: string; label: string; control: "select" | "multiselect" | "switch" | "text";
  options?: { value: string; label: string }[]; value: string | string[];
};

export function useSettingEndpoints() {
  return useQuery({
    queryKey: ["setting-endpoints"],
    queryFn: async (): Promise<{ key: string; label: string }[]> => {
      const { data, error } = await api.GET("/api/opnsense/setting-endpoints");
      if (error || !data) throw new Error("setting endpoints load failed");
      return data as { key: string; label: string }[];
    },
  });
}

export function useTenantDevices() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["devices", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: activeId! } } });
      if (error || !data) throw new Error("devices load failed");
      return data;
    },
  });
}

export function useIntrospectSetting(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (endpointKey: string): Promise<{ fields: SettingField[]; label: string }> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/opnsense/settings/{endpoint_key}",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, endpoint_key: endpointKey } } });
      if (error || !data) throw new Error(t.templates.setting.loadFailed);
      return data as { fields: SettingField[]; label: string };
    },
  });
}
```
NOTE: confirm `api.GET("/api/opnsense/setting-endpoints")` no-arg form; confirm the devices list shape (mirror `DevicesPage`'s query). The introspect is modeled as a mutation because it's user-triggered (Load button) per (endpointKey, deviceId).
- [ ] **Step 3: Run → PASS. Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/templates/settingHooks.ts frontend/src/templates/__tests__/settingHooks.test.tsx
git commit -m "feat(fe): setting hooks (catalog, tenant devices, per-device introspection)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `OpnsenseSettingForm` + kind selector in `TemplateFormModal`

**Files:** Create `frontend/src/templates/OpnsenseSettingForm.tsx`; Modify `frontend/src/templates/TemplateFormModal.tsx`.

**Context:** READ `TemplateFormModal.tsx` (it currently hardcodes `kind: "firewall_alias"` + the alias form). Add a **kind Select** (alias / opnsense_setting) at the top (alongside name + description). If `firewall_alias` → the existing alias fields. If `opnsense_setting` → `<OpnsenseSettingForm value={settingBody} onChange={setSettingBody} />`. On submit, branch by kind: alias → the current body; opnsense_setting → `body = settingBody` ({endpoint_key, payload}), create/update with `kind: "opnsense_setting"`.

- [ ] **Step 1: Implement `frontend/src/templates/OpnsenseSettingForm.tsx`** — a controlled component `({ value, onChange })` where `value = { endpoint_key: string; payload: Record<string, string> }`:
  - A `Select` (testid `setting-endpoint`) of endpoints from `useSettingEndpoints()` (data = `{value: key, label}`), bound to `value.endpoint_key`.
  - A `Select` (testid `setting-device`) of the active tenant's devices from `useTenantDevices()` (data = `{value: d.id, label: d.name}`); local state `deviceId`. If no devices → show `t.templates.setting.noDevice`.
  - A "Load fields" button (testid `setting-load`) → `useIntrospectSetting(deviceId).mutateAsync(value.endpoint_key)` → store the returned `fields` in local state; on error `notifications`.
  - Render the auto-form from `fields` (testid container `setting-fields`): for each field by `control`:
    - `switch` → Mantine `Switch` (checked = payload[path] === "1" ?? field.value === "1"); onChange → payload[path] = checked ? "1" : "0".
    - `select` → `Select` (data = field.options, value = payload[path] ?? field.value); onChange → payload[path] = key.
    - `multiselect` → `MultiSelect` (data = field.options, value = (payload[path] ?? "").split(",").filter(Boolean) OR field.value array); onChange → payload[path] = keys.join(",").
    - `text` → `TextInput` (value = payload[path] ?? field.value); onChange → payload[path] = string.
    Each field gets `data-testid={`setting-field-${path}`}`. Initialize `payload` from the fields' current `value` on load (so the form reflects the device's current values), then the user edits. Call `onChange({ endpoint_key, payload })` on every change.
  - Show `t.templates.setting.hardwareNote` as a small dimmed hint (the backend already omits hardware fields).
- [ ] **Step 2: Modify `TemplateFormModal.tsx`** — add a `kind` to the form state (default "firewall_alias"); a `Select` (testid `tpl-kind`, data = [{value:"firewall_alias",label:t.templates.kindAlias},{value:"opnsense_setting",label:t.templates.kindSetting}]); conditionally render the alias fields (kind==="firewall_alias") or `<OpnsenseSettingForm .../>` (kind==="opnsense_setting") with a local `settingBody` state. On submit, branch by kind to build the right `body` + `kind`. On edit, set the kind from `editing.kind` and prefill (for opnsense_setting, prefill `settingBody` from `editing.body`; the user re-Loads to get the schema, the saved payload values prefill the controls). Keep all existing alias testids working.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit && npm run lint`. (Component tests in Task 4.)
- [ ] **Step 4: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/templates/OpnsenseSettingForm.tsx frontend/src/templates/TemplateFormModal.tsx
git commit -m "feat(fe): OPNsense-setting template form (kind selector + introspection auto-form)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: tests + final

**Files:** Create `frontend/src/templates/__tests__/opnsenseSettingForm.test.tsx`; ensure the existing `templateLibrary.test.tsx` still passes.

- [ ] **Step 1: Write `opnsenseSettingForm.test.tsx`** — render the form inside `withTenant` + `withAuth(superadmin)` (mirror `templateLibrary.test.tsx`). MSW: `GET /api/opnsense/setting-endpoints` → `[{key:"ids_general",label:"IDS — General settings"}]`; `GET /api/tenants/t1/devices` → `[{id:"d1",name:"fw1",...}]`; `GET /api/tenants/t1/devices/d1/opnsense/settings/ids_general` → `{endpoint_key:"ids_general",label:"...",fields:[{path:"general.enabled",label:"enabled",control:"switch",value:"0"},{path:"general.mode",label:"mode",control:"select",options:[{value:"pcap",label:"PCAP"},{value:"netmap",label:"Netmap"}],value:"pcap"}]}`. Test: pick endpoint + device → click Load → the switch + select render (testids `setting-field-general.enabled`, `setting-field-general.mode`); toggling the switch + saving (through the parent modal, OR assert `onChange` payload) produces `payload` with the changed value. (If testing the full modal save is heavy, test the `OpnsenseSettingForm` in isolation asserting `onChange` is called with `{endpoint_key:"ids_general", payload:{...}}` after Load + a field change.)
- [ ] **Step 2: Run the new test + the existing template tests:** `cd frontend && npx vitest run src/templates/__tests__/opnsenseSettingForm.test.tsx src/pages/__tests__/templateLibrary.test.tsx`.
- [ ] **Step 3: Full suite + lint + build:** `cd frontend && npm test && npm run lint && npm run build`. Expected all green (the kind selector defaults to firewall_alias, so the existing alias create test still passes; if the existing test now needs to pick the kind, adjust it minimally).
- [ ] **Step 4: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add frontend/src/templates/__tests__/opnsenseSettingForm.test.tsx
git commit -m "test(fe): OPNsense-setting form (introspect -> auto-form -> payload)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full frontend suite green; lint clean; build green.
- [ ] Final holistic review (focus: the auto-form renders the right control per `control`; payload collects switch/select/multiselect/text correctly; the firewall_alias path unchanged; hardware fields never appear because the backend omits them), then finishing-a-development-branch → PR.
- [ ] After merge: update README (M3 generic introspection template kind shipped).

---

## Self-Review (author)

**Spec coverage (UI §4.5):** the kind selector + the introspection auto-form (endpoint + reference-device + Load → value-controlled controls per inferred field type) (Tasks 2-3); apply/preview reuse the existing template/profile UI (backend preview is kind-aware). Hardware fields are omitted server-side, so the form never shows them.

**Placeholder scan:** Tasks 1-2 carry complete code; Task 3 specifies the component behaviorally with exact testids + the per-control rendering + payload-collection rules + the file to mirror (`TemplateFormModal`); Task 4 names the fixtures + the assertion. The reference-device-from-active-tenant decision is explicit (avoids an org-tenants picker).

**Type consistency:** `SettingField`/the introspection response from the regenerated schema (Task 1); `useSettingEndpoints`/`useIntrospectSetting`/`useTenantDevices` consistent across hooks + the form; `value = {endpoint_key, payload}` is the template body the kind expects; the kind value `"opnsense_setting"` matches the backend.
