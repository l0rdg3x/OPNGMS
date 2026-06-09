# OPNGMS — Phase 4 / Milestone 4C: Firewall-aware Config UI (read-only) — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user authorized proceeding
- **Phase:** 4 of 5 — Milestone 4C (after 4A backup/drift, 4B config model + capabilities)
- **Depends on:** 4B (`/config/model`, `/config/capabilities` APIs) and Milestone D / 2D frontend (shell, i18n, typed client, TanStack Query, Mantine) in `main`
- **Enables:** 4D (edit + push)

---

## 1. Context

**4C** is the first frontend piece of Phase 4: a **read-only, firewall-aware config view** for a
device. It renders the device's config as a navigable tree (from `/config/model`, with sensitive
values masked) and a capability panel (from `/config/capabilities`) that reflects *that* firewall —
its NICs/interfaces, OPNsense version, configured sections, and available plugins/capabilities. It
prepares the ground for editing (4D); here everything is **read-only**.

Builds on the established frontend stack (Vite + React 19 + Mantine v9 + React Router + TanStack
Query, typed `openapi-fetch` client, the lightweight i18n layer, Vitest + RTL + MSW). All UI strings
are **English**, behind the i18n layer.

## 2. Design decisions (4C brainstorming)

| Topic | Decision |
|-------|----------|
| Placement | A **"Config" tab inside `DeviceDetailPage`** — reorganize the page into Mantine Tabs (Info \| Health \| Config) |
| Rendering | **Config tree + capabilities panel** (the firewall-aware view) |
| Sensitive fields | Shown **masked** (`••••` + lock), `value: null`, **read-only** (editing is 4D) |
| Model typing | `/config/model` is `response_model=dict` → a local TS `ConfigNode` type mirrors the backend node shape |

## 3. Structure: `DeviceDetailPage` → Tabs

The current `DeviceDetailPage` (device card + `DeviceHealthSection` + `DeviceActions`) is reorganized
into **Mantine `Tabs`**:
- **Info** (default): the device card + `DeviceActions` (test-connection, delete).
- **Health**: the existing `DeviceHealthSection` (2D charts + time-range selector) — moved verbatim.
- **Config** (new): `CapabilitiesPanel` + `ConfigTree`.

Mantine `Tabs.Panel` mounts only the active panel, so the existing health tests (which assert charts
on render) are updated to click the **Health** tab first. The existing Info-tab tests
(test-connection, delete) keep working with Info as the default tab.

## 4. Data layer

- **Regenerate `schema.d.ts`** to include the 4B endpoints. `/config/capabilities` is typed
  (`CapabilityInventory`); `/config/model` is `dict` (free-form) → define a local recursive TS type:
  ```ts
  export interface ConfigNode {
    tag: string;
    path: string;
    attributes: Record<string, string | null>;
    children: ConfigNode[];
    value: string | null;
    sensitive: boolean;
  }
  ```
- **Hooks** (TanStack Query, tenant-scoped via `useTenant().activeId`):
  - `useConfigModel(deviceId)` → `GET /api/tenants/{tenant_id}/devices/{device_id}/config/model`
    (returns `ConfigNode`). A 404 (no snapshot yet) is surfaced as an empty state, not an error.
  - `useConfigCapabilities(deviceId)` → `GET .../config/capabilities` (returns the typed inventory).
  - Both `enabled: !!activeId && !!deviceId`; query keys namespaced per tenant; throw on non-404 API
    errors (consistent with the 2D hooks), but treat 404 as "no data".

## 5. Components (Config tab)

- **`CapabilitiesPanel`**: a card from `/config/capabilities` — OPNsense version, interfaces/NICs
  (logical name → NIC + description), configured sections, and **available capabilities** with a
  badge distinguishing *configured* vs *available-not-yet-configured*. Resilient to an empty
  `available_capabilities` (probe failed → empirical-only).
- **`ConfigTree`** + **`ConfigNode`** (recursive, collapsible via Mantine `Collapse` + a chevron
  toggle): renders the tree from `/config/model`.
  - Leaf node: `tag: value`. Container node: expandable, shows child count.
  - **Sensitive node**: rendered **masked** (`••••` + a lock icon), `value` is null (never received),
    **read-only**. Indexed-sibling tags (`tag[n]`) display naturally.
  - Default expansion: top levels expanded a couple of levels, deeper collapsed (avoid a wall of text).

## 6. Data flow & states

- Loading → Mantine skeleton; error (non-404) → Mantine `Alert`; **empty** (404 / no snapshot yet) →
  a friendly message ("No configuration captured yet" — the daily backup will populate it).
- Tenant-scoped: hooks read `activeId`; tenant change refetches (query keys include `activeId`).
- Read-only: no mutations, no CSRF.

## 7. i18n & testing

- All strings English, keyed under `config.*` in `src/i18n/en.ts`, via `useT()`.
- MSW handlers for `/config/model` and `/config/capabilities`; Vitest + RTL:
  - `ConfigTree`/`ConfigNode`: renders nodes; a sensitive node shows masked + lock and **no value**;
    expand/collapse toggles children; the secret string never appears in the DOM.
  - `CapabilitiesPanel`: shows version/interfaces/sections/available; configured-vs-available badges.
  - `DeviceDetailPage`: tab switching (Info/Health/Config); Config tab loads model + capabilities;
    empty-state on 404; existing Info/Health tests updated for the tab structure.

## 8. Milestone 4C breakdown (for the plan)
1. **Data layer**: regen `schema.d.ts` + hooks (`useConfigModel`/`useConfigCapabilities`) + `ConfigNode` type.
2. **`CapabilitiesPanel`** (+ i18n) + tests.
3. **`ConfigTree`/`ConfigNode`** recursive (collapsible, sensitive masked, read-only) + tests.
4. **Reorganize `DeviceDetailPage` into Tabs** + mount the Config tab + update existing tests.

Each task = TDD + subagent-driven review.

## 9. Definition of "Done" (4C)
- `DeviceDetailPage` has Info / Health / Config tabs; existing functionality intact.
- The Config tab renders the device's config tree (sensitive values masked + read-only) and a
  capabilities panel (interfaces, version, configured sections, available capabilities).
- Loading/error/empty (no-snapshot) states handled; tenant-scoped (tenant change refetches).
- No secret value ever appears in the DOM; everything read-only.
- Frontend suite (Vitest) green; `npm run build` + `npm run lint` clean.

## 10. Non-goals (4C) / deferred
- **Editing / push** (4D) — sensitive fields are masked + read-only here.
- **Path search/filter** of the tree — deferred.
- **Raw config download** — never (secrets).
- **Per-field edit forms / version-aware widgets** (4D, device-sourced schema).

## 11. Open questions (non-blocking)
- **Tree default-expansion depth** — pick a sensible default (e.g. 2 levels); refine with real configs.
- **Large configs** — a very large tree may warrant virtualization/search later (deferred; config is
  moderate in size).
