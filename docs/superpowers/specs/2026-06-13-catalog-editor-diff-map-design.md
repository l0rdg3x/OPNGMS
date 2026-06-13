# Sub-project 3c (final) — cross-version diff badges + live config.xml map — design

## Where this fits

The killer feature is a version/edition-aware OPNsense config editor. Done: 1 (catalog generator), 2
(distribution + apply engine, #94), the 6h publish Action (#96), 3a (editor foundation, #97), 3b
(OPNsense-like nav + live options, #98/#99). **3c is the final stage**, adding two enrichments to the
editor:

1. **Cross-version diff badges** — surface "new / changed since version X" on models and fields, so an
   operator sees what the editor's version changed versus an earlier release.
2. **Live `config.xml` map (cross-referenced)** — a read-only navigable view of the device's **live**
   config.xml covering ALL settings, each node cross-referenced to the catalog: editable-in-catalog
   (jump to the editor) or read-only (no API coverage — legacy/non-MVC).

Both reuse a lot: the generator's `diff_catalogs` logic, the catalog provider (#94), `build_tree` +
the redaction in `config_model.py`, the connector's `get_config_backup`, the Editor tab (#97/#98).

## Decisions taken with the user (2026-06-13)

- **Diff baseline:** on-demand server-side diff of the device's catalog version vs a baseline, default
  the **previous published version**, operator-**selectable**. Both catalogs are already DB-cached.
- **Config.xml map = cross-reference** the navigable tree with the catalog (editable → link to the
  editor model form; otherwise read-only), connecting the two halves of the feature.
- **Map source = LIVE** — fetch the device's config.xml live on open; if the device is unreachable, fall
  back to the latest stored snapshot, clearly labelled **stale** (a read-only view, so a stale fallback
  is acceptable — unlike editing).

## Part A — Cross-version diff badges

### Backend
- **`services/catalog_versions.py`** (NEW, pure) — `diff_catalogs(a: dict, b: dict) -> dict` returning
  `{added_models, removed_models, models: {mid: {added_fields, removed_fields, changed_fields}}}` (a
  field change compares `type`/`required`/`default`/`options`). Mirrors the generator's `tools` diff,
  reimplemented app-side because `tools/` is offline tooling, not a runtime dependency.
- **`services/catalog_provider.py`** — add pure `previous_version(versions, version) -> str | None`
  (highest published version strictly `<` the device version), reusing the existing `_parse_version`.
- **Endpoint** `GET /api/tenants/{tid}/devices/{did}/catalog/diff?from=<version>` (`DEVICE_VIEW`,
  tenant-scoped + ownership guard): resolve the device's (edition, version) → catalog; resolve the
  `from` catalog (default `previous_version`); return
  `{from, to, available_baselines, diff: <diff_catalogs output>}`. 404 if no catalog; if `from` is
  absent/unresolvable, return an empty diff with `from: null`.

### Frontend
- `useCatalogDiff(deviceId, from)` hook + a baseline `Select` (the `available_baselines`) at the top of
  the Editor tab. The diff is fetched once per (device, baseline) and shared across models.
- `CatalogModelForm` renders a small badge per field: in the model's `added_fields` → **"New since
  {from}"**; in `changed_fields` → **"Changed since {from}"**. `CatalogMenuTree` shows a dot/count on a
  model whose id is in the diff (`added_models` or `models[mid]` non-empty).
- No baseline / same version → no badges (graceful).

## Part B — Live config.xml map (cross-referenced)

### Backend
- **`services/config_map.py`** (NEW, pure) — `annotate_with_catalog(tree: dict, catalog: dict) -> dict`:
  walk the `build_tree` output; for each node whose path falls under a catalog model's **`xml_path`**
  (the config.xml mount, e.g. `OPNsense/unboundplus`), set `catalog_model_id` = that model id (and
  `editable: true`); nodes with no covering model get `editable: false`. Model-level granularity (the
  whole subtree of a model's mount is "editable via model X"); field-level deep-link is optional/out of
  scope. Index-suffixed paths (repeated siblings) are matched on their tag prefix.
- **Endpoint** `GET /api/tenants/{tid}/devices/{did}/config/map` (`DEVICE_VIEW`, ownership guard):
  - fetch the device's config.xml **live** (`client.get_config_backup()`), `build_tree` it, resolve the
    device's catalog (provider), `annotate_with_catalog`. Returns `{tree, source: "live", reachable:
    true}`.
  - on any connector/credential/parse error → fall back to the latest **snapshot** (decrypted
    server-side, same as `config/model`), annotated, with `source: "snapshot", reachable: false,
    taken_at`. No snapshot either → 404 "no config available".
  - reuses the existing redaction in `build_tree` (secrets never leave the server) and the existing
    snapshot decrypt path (`_xml`).

### Frontend
- A **"Config map"** view in the Editor tab (a toggle in the left pane between **Menu** and **Config
  map**, or a sub-tab). Renders the annotated tree (reuse/extend the existing `ConfigTree`):
  - a node with `catalog_model_id` shows an **"Edit in catalog"** affordance → selects that model in the
    editor (switches to Menu view + opens the model form);
  - a node with `editable: false` shows a muted **"read-only (no API)"** marker;
  - a `source: "snapshot"` response shows a **"stale — device unreachable, last backup {taken_at}"**
    banner.
- The existing snapshot-based `config/model` view in the **Config tab stays as-is** (unchanged); 3c's
  live cross-referenced map is the editor-integrated one.

## Safety / security

- Both endpoints are `DEVICE_VIEW`, tenant-scoped (RLS) + the explicit ownership guard; neither builds a
  connector for another tenant's device. The diff is static catalog content. The map reuses
  `build_tree`'s conservative secret redaction — no secret value leaves the server. No mutation surface
  (read-only); editing still goes through the unchanged create endpoint.
- The live config fetch degrades to the snapshot (or 404) on any connector/parse error — no stack/secret
  leak (mirrors the existing `drift-check` degradation).

## Testing
- **Diff:** pure `diff_catalogs` (added/removed/changed models + fields); `previous_version`
  (strict-less floor / none). Endpoint: device-vs-previous, explicit `from`, no-baseline → empty,
  no-catalog → 404, cross-tenant → 404.
- **Map:** pure `annotate_with_catalog` (node under a model's xml_path → editable+model_id; outside →
  read-only; index-suffixed paths). Endpoint: live tree annotated (respx-mocked config.xml); unreachable
  → snapshot fallback labelled stale; no snapshot → 404; redaction preserved; cross-tenant → 404.
- **Frontend:** diff badges on added/changed fields + a menu dot; baseline selector drives the hook;
  the config-map view renders editable vs read-only markers, "Edit in catalog" selects the model, the
  stale banner shows on a snapshot source.

## Out of scope (final stage)
- Field-level deep-link from a config-map node to the exact field in the model form (model-level jump
  only); diffing config.xml **values** over time (the existing snapshot `structural_diff` already does
  that in the Config tab); baking `since` tags into the catalog at generation (on-demand diff chosen);
  plugin coverage beyond the core catalog.
