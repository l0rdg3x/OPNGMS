# OPNGMS — Phase 4 / Milestone 4D-c: Config Editing UI — Design Spec

- **Date:** 2026-06-09
- **Status:** Approved (design); the user delegated decisions and authorized proceeding autonomously
- **Phase:** 4 of 5 — Milestone 4D-c (the editing UI for the dry-run push pipeline)
- **Depends on:** 4D-a (change/push API: create/preview/schedule/list/cancel, CONFIG_PUSH) and 4C (Config tab) in `main`
- **Enables:** end-to-end editing UX over the dry-run pipeline (real push is 4D-b)

---

## 1. Context

**4D-c** gives the Config tab (4C) the ability to **propose, preview, schedule, and cancel** granular
firewall **alias** changes, consuming the 4D-a pipeline. Everything is **dry-run** server-side (no
firewall mutation until 4D-b), so this is safe to ship: an operator can drive the full workflow and
see changes move through `draft → scheduled → applied|conflict` (dry-run) statuses.

Builds on the 2D/4C frontend (React 19 + Mantine v9 + TanStack Query + typed `openapi-fetch` client +
i18n + Vitest/RTL/MSW). All UI strings English, behind i18n.

## 2. Design decisions (4D-c)

| Topic | Decision |
|-------|----------|
| Placement | A **"Pending changes" panel** + a **"Propose alias change"** action in the **Config tab** (4C) |
| Change form | Modal: `operation` (add/set/delete), `name`, `type` (host/network/port/url), `content` (one per line) → creates a `draft` |
| Preview | On-demand, shows the **secret-safe** summary from `GET .../preview` |
| Schedule | Apply **immediately** or pick a **date/time** (Mantine **`@mantine/dates` `DateTimePicker`**) → `POST .../schedule` |
| Cancel | Cancel a `draft`/`scheduled` change |
| RBAC | Backend enforces `CONFIG_PUSH`; the UI **hides the editing actions for `read_only`** (role from `useTenant()`), and surfaces a 403 as an `Alert` if it slips through |

## 3. Data layer

- **Regenerate `schema.d.ts`** for the 4D-a endpoints (create/preview/schedule/list/cancel +
  `ConfigChangeIn`/`ScheduleIn`/`ConfigChangeOut`).
- **Hooks** (TanStack Query, tenant-scoped via `useTenant().activeId`):
  - `useConfigChanges(deviceId)` → `GET .../config/changes` (list, newest-first).
  - `useCreateChange(deviceId)` → mutation `POST .../config/changes` → invalidates the list.
  - `useScheduleChange(deviceId)` → mutation `POST .../config/changes/{id}/schedule` (body
    `{ scheduled_at? }`) → invalidates.
  - `useCancelChange(deviceId)` → mutation `POST .../config/changes/{id}/cancel` → invalidates.
  - `usePreviewChange(deviceId, changeId)` → `GET .../config/changes/{id}/preview` (enabled on demand).
  - Mutations throw on error (consistent with 2D); a **403** surfaces as an `Alert` (the user lacks
    `CONFIG_PUSH`). CSRF header is added automatically by the client middleware on POSTs.

## 4. Components (Config tab)

- **`ChangesPanel`** (`src/config/ChangesPanel.tsx`): the device's changes from `useConfigChanges`,
  as a table — kind, operation, target, **status badge** (draft=gray, scheduled=blue, applying=yellow,
  applied=green, conflict=orange, failed=red, cancelled=dimmed), scheduled-at — with per-row actions
  (Preview / Schedule / Cancel) enabled by status (`draft`/`scheduled` are actionable). Header has the
  **"Propose alias change"** button. Hidden actions for `read_only`.
- **`ProposeAliasModal`** (`src/config/ProposeAliasModal.tsx`): a Mantine modal with a `@mantine/form`
  form: operation `SegmentedControl` (add/set/delete), `name` `TextInput`, `type` `Select`
  (host/network/port/url), `content` `Textarea` (one entry per line → array). On submit → `useCreateChange`
  with `kind:"alias"`, `payload:{name,type,content:[...]}` → close + refetch.
- **`SchedulePopover`/inline control**: "Apply now" vs a `DateTimePicker` (min = now) → `useScheduleChange`
  with `scheduled_at` (null for immediate).
- **`PreviewModal`**: shows the `usePreviewChange` result (operation/kind/target/new payload) read-only.

## 5. Data flow & states

- Loading → skeleton/`Loader`; error → `Alert`; **empty** → "No pending changes".
- After create/schedule/cancel → invalidate `["config-changes", activeId, deviceId]` so the panel
  refreshes the status.
- Tenant-scoped (hooks key on `activeId`); read-only: the propose/schedule/cancel controls are hidden.
- Note: since the pipeline is **dry-run**, a scheduled change runs the apply job server-side but does
  not touch a firewall; the panel reflects `applied`(dry-run)/`conflict` after the worker runs (the
  UI just shows the status; it does not wait synchronously).

## 6. i18n & testing

- All strings English, under `config.*` (extend the existing group) + a `changes.*` subgroup.
- MSW handlers for the 5 change endpoints; Vitest + RTL:
  - `ChangesPanel`: renders the list + status badges; actions enabled by status; empty-state;
    read-only hides actions.
  - `ProposeAliasModal`: fills + submits → POST create called with the right payload; content textarea
    → array; modal closes.
  - Schedule: immediate → `scheduled_at: null`; with a picked date → `scheduled_at` sent.
  - Cancel → POST cancel called; 403 on schedule → `Alert` shown.

## 7. Milestone 4D-c breakdown (for the plan)
1. **Data layer**: regen schema + hooks/mutations + `@mantine/dates` install + `ConfigChange` TS type.
2. **`ChangesPanel`** (list + status badges + empty/loading; actions wired in Task 4) + i18n + tests.
3. **`ProposeAliasModal`** (form → create) + tests.
4. **Preview + Schedule (DateTimePicker) + Cancel** wired into `ChangesPanel`, mounted in the Config tab,
   read-only hiding + 403 handling + tests.

## 8. Definition of "Done" (4D-c)
- The Config tab shows a Pending-changes panel and lets an operator propose an alias change, preview it
  (secret-safe), schedule it immediately or for a date/time, and cancel it.
- Status badges reflect the pipeline; read-only users don't see the editing actions; a 403 is handled.
- Tenant-scoped; no secret value in the DOM; frontend suite green + `npm run build`/`lint` clean.

## 9. Non-goals (4D-c) / deferred
- **Real firewall push** (4D-b) — the pipeline stays dry-run.
- **Inline tree-node editing** — changes are proposed via the form (the tree stays read-only here).
- **Section types beyond aliases** (4D-d).
- **Live status polling** — the panel refetches on action; a `refetchInterval` is a later nicety.

## 10. Open questions (non-blocking)
- **Alias `type`/`content` field shape** — TO VERIFY against the real OPNsense alias API (4D-b); the
  form uses a plausible shape (name/type/content[]).
- **Conflict UX** — a `conflict` status currently just shows a badge; a "re-baseline & retry" flow is a
  later enhancement.
