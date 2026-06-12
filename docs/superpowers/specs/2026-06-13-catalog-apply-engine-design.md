# Sub-project 2 — catalog distribution + generic apply engine — design

## Where this fits

The killer feature = a version/edition-aware generic OPNsense config editor. Sub-project 1 (the
offline **catalog generator**, PR #87) is done. This sub-project 2 makes the catalog **available to
the running app** and adds a **catalog-driven apply** so a config change can target **any** model in
the catalog. Sub-project 3 (the editor UI) consumes both.

## Decisions taken with the user (2026-06-13)

- **Distribution:** catalogs are NOT committed to the repo. They're published as **GitHub Release
  assets** on the OPNGMS repo; the app **fetches them dynamically**, caches in the DB, and verifies a
  **SHA-256 manifest**. This decouples catalog updates from app releases (coverage grows when OPNsense
  ships a new version, without an OPNGMS release).
- **Apply kind:** a **new `catalog_setting`** change kind (coexists with the curated, template-driven
  `opnsense_setting`). Editor = catalog/everything; templates = curated/portable.
- **Scope v1:** **scalar settings AND grids** (ArrayField add/set/del).
- **Safety:** a **denylist** of lockout-risk models the editor won't push.

## Part A — Catalog distribution (publish to GitHub Releases)

A publish step (a generator CLI subcommand + a documented ops run; later a CI workflow on new OPNsense
tags) produces, for a set of versions:

- `community-<version>.json` (one per version) — full catalogs.
- `manifest.json` — `{"edition/version": "<sha256 of the catalog file>", ...}` + a `generated_at`.

These are uploaded as assets to a **rolling release** tagged `catalogs` (assets replaced on republish).
The app fetches from `<base>/manifest.json` and `<base>/community-<version>.json` where `<base>` =
`https://github.com/<owner>/<repo>/releases/download/catalogs` (configurable). Publishing a new version
= regenerate + re-upload the asset + update the manifest; **no app release**.

**CLI additions** (`tools/opnsense_catalog/cli.py`):
- `generate-all --edition community --versions 26.1.7,26.1.8 --out-dir <dir>` — emit every catalog +
  `manifest.json` (sha256 per file).
- (Upload to the release is done with `gh release upload catalogs <dir>/* --clobber` — an ops step,
  documented in the tool README; not app code.)

## Part B — Catalog provider (app-side: fetch + cache + verify)

**Settings** (`core/config.py`): `catalog_release_base_url` (default the OPNGMS `catalogs` release
URL), `catalog_auto_fetch: bool = True`.

**`catalog_cache` table** (NEW, global, non-RLS — superadmin/worker + provider only; migration +
app-role grants like `smtp_settings`): `id`, `edition`, `version`, `sha256`, `content` (JSONB),
`fetched_at`, unique `(edition, version)`.

**`services/catalog_provider.py`**:
- `resolve_version(manifest, edition, version) -> str | None` (pure) — exact match, else the **floor**
  (highest published version ≤ the device version) for that edition; None if none ≤.
- `async get_catalog(session, edition, version) -> dict | None`:
  1. If a cache row for `(edition, resolved_version)` exists → return its `content`.
  2. Else (and `catalog_auto_fetch`): fetch `manifest.json`; resolve the version; fetch
     `community-<resolved>.json`; **verify** its SHA-256 == the manifest entry (reject + log on
     mismatch); cache `content` + `sha256`; return.
  3. On any network/integrity error: return the cached copy if present, else **None** (caller/editor
     degrades to "catalog unavailable for this version").
- `async get_model(session, edition, version, model_id) -> dict | None` — convenience: the model from
  the catalog (or None).
- All HTTP via `httpx` with a timeout + `follow_redirects=True` (GitHub release downloads redirect).

**Integrity:** the catalog file's SHA-256 is checked against `manifest.json` (fetched over HTTPS from
the same release) **before** it is cached or used — a tampered/truncated download is rejected.

## Part C — Generic apply (`catalog_setting`)

**Resolve at PROPOSAL time, apply dumbly.** When a `catalog_setting` change is created, the model's
endpoints are resolved from the catalog (via the provider, for the device's resolved edition+version)
and **embedded** in the change payload. The applier is device-independent (matches the existing
`apply_for_kind(client, kind, operation, payload, dry_run)` signature) and just calls the connector.

**Change payload shape** (`catalog_setting`):
```json
{
  "model_id": "unbound",
  "set_path": "unbound/settings/set",
  "reconfigure_path": "unbound/service/reconfigure",
  "model_root": "unbound",
  "scalars": {"general.enabled": "1", "general.port": "53"},
  "grids": [
    {"op": "add", "endpoints": {"add": "unbound/settings/addHostOverride", ...}, "item": {...}},
    {"op": "set", "endpoints": {...}, "uuid": "<row-uuid>", "item": {...}},
    {"op": "del", "endpoints": {"del": "unbound/settings/delHostOverride"}, "uuid": "<row-uuid>"}
  ]
}
```
`scalars` and `grids` are both optional; embedding the resolved endpoints keeps the change auditable
and pins it to the catalog version at proposal time.

**Connector** (`connectors/opnsense/client.py`):
- Reuse `apply_setting(set_path, reconfigure_path, model_root, scalars, dry_run)` for the scalar part.
- Add `apply_grid_item(op, endpoints, *, uuid=None, item=None, dry_run) -> dict` — `add` → POST
  `endpoints['add']` `{<row>: item}`; `set` → POST `endpoints['set']/{uuid}` `{<row>: item}`; `del` →
  POST `endpoints['del']/{uuid}`. uuid charset-validated (anti path-injection, like the existing del*
  methods). The single `reconfigure` runs once after all ops.

**Applier** (`services/catalog_kind.py`): `register_change_applier("catalog_setting", …)`. Applies
`scalars` (if any) via `apply_setting` (dry_run honoured), then each `grids` op via `apply_grid_item`,
then one reconfigure. Dry-run performs no mutation. Embedded paths are charset-validated.

**Create endpoint** (for the editor, sub-project 3 calls it):
`POST /api/tenants/{tid}/devices/{did}/catalog/changes` (`CONFIG_PUSH` + CSRF):
- resolve the device's (edition, version) → catalog via the provider; 404 if no catalog.
- look up `model_id`; **422** if unknown or in the **denylist** (`CATALOG_DENYLIST` — e.g. interface
  assignment models: `interfaces`, anything that can isolate the box); validate the `scalars` keys are
  known catalog fields and not `exclude_fields`; validate grid ops reference known grids.
- build the embedded payload + `create_change(kind="catalog_setting", …)` (draft) → the operator then
  schedules it through the **existing** pipeline (snapshot/staleness/revert/drift all already apply).

**Read endpoint** (the editor needs the schema + live values — minimal here, fleshed out in sub-3):
`GET /api/tenants/{tid}/devices/{did}/catalog` → `{edition, version, resolved_version, models: [...] }`
(the catalog for the device, denylist-flagged). Live values come from the device `get` at edit time
(sub-project 3).

## Safety rails

- `CATALOG_DENYLIST` (code constant, v1): models that can lock the operator out of the box (interface
  assignment, and any flagged high-risk). The create endpoint refuses them (422); the read endpoint
  marks them `read_only: true`.
- The whole path still sits behind the default-OFF `LIVE_PUSH_ENABLED` master switch + the pre-apply
  snapshot + targeted revert + the staleness guard — unchanged.
- The applier never echoes raw config values into errors; the create endpoint is tenant-scoped (RLS).

## Testing

- **Provider:** pure `resolve_version` (exact/floor/none); `get_catalog` fetch+verify (respx-mocked
  release: manifest + catalog) caches; SHA-256 mismatch → rejected (not cached); offline with a cache
  row → served; offline cold → None.
- **Connector:** `apply_grid_item` add/set/del request shapes (respx); uuid charset guard; dry_run no-op.
- **Applier:** `catalog_setting` applies scalars + grids + one reconfigure; dry_run; unknown/denylist
  paths rejected.
- **API:** create resolves+validates+creates a draft (RLS app-role client); unknown model/denylist →
  422; no catalog for the device → 404; cross-tenant device → 404; read returns the device's catalog.
- **CLI:** `generate-all` emits N catalogs + a correct sha256 manifest (vendored mini source, no network).

## Out of scope (this sub-project)

- The editor UI (sub-project 3) — forms from the catalog + live values + version-diff surfacing.
- Dynamic option resolution (interface/alias/CA lists) at edit time — sub-project 3 (the create
  endpoint accepts the chosen values; resolving the *choices* is the UI's job).
- Business/proprietary catalogs; a CI workflow that auto-publishes on new OPNsense tags (documented
  ops run for now); resolving `.\X` module-local field classes.
