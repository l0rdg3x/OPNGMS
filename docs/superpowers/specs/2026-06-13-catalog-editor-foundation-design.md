# Sub-project 3a — catalog editor foundation (live single-model editing) — design

## Where this fits

The killer feature = a version/edition-aware generic OPNsense config editor. Sub-projects 1 (catalog
generator, #87) and 2 (distribution + generic apply engine, #94) are done: the app can fetch a
device's versioned catalog and push any catalog setting through the config pipeline. **Sub-project 3
is the editor UI**, and the user chose the full **OPNsense-like console** as the goal. That is too
large for one spec, so it is staged:

- **3a (this spec)** — the **editing foundation**: open any catalog model on a device, see its **live
  current values** in a generated form, edit scalars **and grids**, propose a change through the
  existing pipeline. A flat, searchable model list (no full navigation yet).
- **3b** — OPNsense-like **navigation**: a menu tree rebuilt from OPNsense's `Menu.xml` + global
  search + visual parity.
- **3c** — cross-version **diff badges** + the read-only **live `config.xml` map** (coverage of
  settings the catalog can't write).

## Decisions taken with the user (2026-06-13)

- **Goal = full OPNsense-like console**, built in the 3a → 3b → 3c stages above; 3a first.
- **Live values:** the form is prefilled from a **live `get` against the device** on open (the user's
  reason: someone may have changed the config after the last backup). If the device is **unreachable**,
  the form is **not** prefilled and editing is disabled; the latest `config.xml` snapshot may be shown
  **read-only**, clearly labelled stale (never used as an editable baseline).
- **Grids in 3a:** yes — ArrayField add/edit/delete is included (the apply engine already supports it).

## Backend

### New endpoint — live model values

`GET /api/tenants/{tid}/devices/{did}/catalog/models/{model_id}` (`DEVICE_VIEW`; tenant-scoped + the
explicit `device.tenant_id == tenant_id` ownership guard, like the other catalog routes).

1. Resolve the device's catalog via the existing provider (`catalog_provider.get_catalog`); **404** if
   no catalog for the device's version.
2. Look up `model_id` in the catalog; **404** if unknown.
3. If the model is in `CATALOG_DENYLIST`, return it with `read_only: true` and **skip** the live read
   (no editing anyway).
4. Else call the device **live**: `client.get_setting(model["endpoints"]["get"])`. Flatten the
   response to the catalog's field paths and extract grid rows. On any connector/credential error,
   return `reachable: false` with empty `values`/`grids` (the UI disables editing).

Response shape:
```json
{
  "model": { "...the catalog model (fields, grids, pages, endpoints)..." },
  "values": { "general.enabled": "1", "general.port": "53" },
  "grids": { "hosts": [ { "uuid": "ab-12", "hostname": "web", "server": "10.0.0.10" } ] },
  "reachable": true,
  "read_only": false
}
```
`model` is included so the frontend has schema + values + denylist flag in one call.

### New service — `services/catalog_live.py` (pure, device-independent)

- `flatten_values(get_response: dict, model: dict) -> dict[str, str | list[str]]` — walk the device
  `get` response under `model["model_root"]` and return `{dotted_path: current_value}` for scalar
  leaves. Reuses the **option-object normalization** already in `services/setting_introspect.py`
  (`_is_option_dict`/`_options`/`_selected`): an option-dict → its selected key (a list for multi),
  `"0"/"1"`/plain string → the string, nested object → recurse. Grid (uuid-keyed) nodes are skipped
  here (handled below).
- `extract_grid_rows(get_response: dict, model: dict, grid: dict) -> list[dict]` — locate the grid's
  node (`grid["path"]`) under the model root; an OPNsense grid node is a **uuid-keyed dict**
  `{uuid: {field: value_or_option_dict}}`. Return `[{"uuid": uuid, <field>: normalized_value, ...}]`,
  normalizing each cell with the same option-object logic.
- The three shared helpers (`_is_option_dict`, `_options`, `_selected`) are factored into a tiny
  shared util (`services/opnsense_values.py`) imported by both `setting_introspect.py` and
  `catalog_live.py` — DRY, no behaviour change to the existing introspection.

### Reuse (unchanged)

- `POST …/catalog/changes` (#94) creates the draft change from the editor's `CatalogChangeIn`.
- The existing schedule/snapshot/staleness/revert pipeline + `ChangesPanel` handle everything after
  the draft is created. 3a only **creates** drafts.

## Frontend — the "Editor" tab

A new device-page tab **"Editor"** (a new `frontend/src/catalog/` module), shown alongside the existing
Config/Firmware/etc. tabs on `DeviceDetailPage`.

- `CatalogEditorTab.tsx` — two panes. **Left:** a searchable list of the device's catalog models
  (from `GET …/catalog`: id/title, denylist badge). **Right:** the selected model's form.
- `CatalogModelForm.tsx` — on model select, fetches `GET …/catalog/models/{id}`; renders the model's
  `pages` (each page = a group of fields) then its grids. Tracks **dirty** fields/rows. A footer with
  **Preview** (a **client-side** summary of the pending diff — changed scalar paths + grid ops — no
  server call; the server-side `preview_change` only exists for an already-created change) and
  **Propose**.
- `CatalogFieldInput.tsx` — maps a catalog field **type** → a Mantine input, prefilled with the live
  value: `bool`→`Switch`, `int`→`NumberInput`, `enum`→`Select` (catalog `options`), `multienum`→
  `MultiSelect`, `string`/`network`/`ref`/`raw`→`TextInput`. (`ref` dynamic option lists stay text in
  3a — live option resolution is 3b.)
- `CatalogGridTable.tsx` — a table of the grid's current rows with **add / edit / delete**; add/edit
  open a row modal built from the grid's own fields (same `CatalogFieldInput`). Edits accumulate as
  grid ops (`add`/`set` with the row item, `del` with the uuid).
- `catalogHooks.ts` — react-query hooks: `useDeviceCatalog(did)` (`GET …/catalog`), `useCatalogModel
  (did, modelId)` (`GET …/catalog/models/{id}`), `useProposeCatalogChange(did)` (`POST …/catalog/
  changes`, CSRF via the shared client).

### Flows

- **Propose:** the form diffs against the live values and builds a `CatalogChangeIn` with **only**
  changed scalars + explicit grid ops, then POSTs. On success the draft appears in the device's
  existing **Changes** panel (schedule → apply → revert already there). A success notice links there.
- **Read-only / denylist:** the model renders with inputs disabled and a "not editable (safety
  denylist)" note; Propose is hidden.
- **Unreachable:** a banner "device unreachable — live values are required to edit"; the form renders
  the schema disabled (no values). Optionally a read-only "last snapshot (stale)" peek — deferred to
  3c with the config.xml map; 3a just shows the banner.

## Security

- The model endpoint is `DEVICE_VIEW` + tenant-scoped (RLS) + the explicit ownership guard; it never
  builds a connector for another tenant's device (same pattern as `config_capabilities`).
- It returns **field values** of the device's own config to a same-tenant viewer — consistent with the
  existing `config/model` and drift endpoints. No cross-tenant exposure.
- Proposing still goes through `POST …/catalog/changes` (CONFIG_PUSH + CSRF + denylist 422 +
  path/uuid-guarded apply) and the default-OFF `LIVE_PUSH_ENABLED` switch — unchanged.
- The live read degrades to `reachable:false` on any connector/credential error; no secret/stack leak.

## Testing

- **Backend (pure):** `flatten_values` — option-dict→selected (single + multi), `0/1`/string, nested
  recursion; `extract_grid_rows` — uuid-keyed node → rows with normalized cells; empty/missing node → `[]`.
- **Backend (API, respx-mocked device):** model endpoint returns schema+values+grids when reachable;
  unreachable → `reachable:false`, empty; unknown model → 404; denylist model → `read_only:true`, no
  live call; cross-tenant device → 404; no catalog → 404. App-role (RLS) client.
- **Frontend (vitest + RTL):** `CatalogFieldInput` renders the right control per type with the live
  value; dirty-tracking builds the correct `CatalogChangeIn` (only changed scalars); `CatalogGridTable`
  add/edit/delete produce the right grid ops; read-only and unreachable states disable Propose.

## Out of scope (this stage)

- OPNsense-like menu navigation + global search — **3b** (needs an OPNsense `Menu.xml` harvest added to
  the generator/distribution).
- Cross-version diff badges ("new/changed since 26.1.7") + the read-only live `config.xml` map — **3c**.
- Dynamic option-list resolution for `ref`/interface/alias/CA fields (rendered as text in 3a) — **3b**.
- Bulk/multi-model edits; profile-style bundles of catalog changes (the existing profiles cover
  curated kinds).
