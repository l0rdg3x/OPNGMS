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
- **Business edition:** no separate catalog is generated. Each BE release is based on a known Community
  release (stated on its docs page), so a small published `business-base.json` map resolves a Business
  device → its Community base → the **Community catalog** (the shared MVC/API core). Proprietary BE
  deltas stay out of scope (sub-project 4, blocked on a Business box).
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
- `business-base --out <dir>/business-base.json` — scrape the OPNsense docs to emit the
  **Business→Community base** map (see below). A documented ops step, run at publish time.
- (Upload to the release is done with `gh release upload catalogs <dir>/* --clobber` — an ops step,
  documented in the tool README; not app code.)

### Business↔Community association (`business-base.json`)

OPNsense **Business Edition** ships no separate API-model source: each BE release is a hardened
snapshot of a **specific Community release**, stated verbatim on its release page
`docs.opnsense.org/releases/BE_<version>.html` ("This business release is based on the OPNsense
X.Y.Z community version …"). BE trails Community by ~6 months on an April/October cadence — verified
**BE 26.4 → CE 26.1.6**, BE 25.10 → CE 25.7.x, BE 25.4 → CE 25.1.x.

We exploit this: a Business device is served the **Community catalog of its base version** (the shared
core — the only part exposed over MVC/API; proprietary plugins are out of scope for v1, per the user).
The publish step emits one extra small asset:

`business-base.json`:
```json
{
  "generated_at": "2026-06-13T00:00:00Z",
  "map": { "26.4": "26.1.6", "25.10": "25.7.9", "25.4": "25.1.12" }
}
```
`map` is `business_version → community_base_version`. The `business-base` CLI subcommand builds it by
fetching the BE release index + each `BE_<v>.html` and extracting the "based on the OPNsense X.Y.Z
community version" line (regex). It is uploaded alongside `manifest.json` to the `catalogs` release.

## Part B — Catalog provider (app-side: fetch + cache + verify)

**Settings** (`core/config.py`): `catalog_release_base_url` (default the OPNGMS `catalogs` release
URL), `catalog_auto_fetch: bool = True`.

**`catalog_cache` table** (NEW, global, non-RLS — superadmin/worker + provider only; migration +
app-role grants like `smtp_settings`): `id`, `edition`, `version`, `sha256`, `content` (JSONB),
`fetched_at`, unique `(edition, version)`.

**`services/catalog_provider.py`**:
- `resolve_version(versions, version) -> str | None` (pure) — exact match against a sorted version
  list, else the **floor** (highest published version ≤ the device version); None if none ≤. Used for
  both the manifest's Community versions and the `business-base` map's BE versions.
- `resolve_target(manifest, business_base, edition, version) -> (str, str) | None` (pure) — returns
  the `(resolved_edition, resolved_version)` whose catalog to serve:
  - `community`: `("community", resolve_version(manifest_versions, version))`.
  - `business`: floor-resolve `version` in `business_base["map"]` → the Community base, then
    floor-resolve **that** in the manifest → `("community", <community_version>)`. A Business device
    is always served a **Community** catalog (shared core). None if either step finds nothing ≤.
- `async get_catalog(session, edition, version) -> dict | None`:
  1. **Resolve** (network): fetch `manifest.json` (+ `business-base.json` when
     `edition == "business"`) and compute `target = resolve_target(...)`. On a manifest/business-base
     fetch failure, `target = None`.
  2. **Warm cache hit:** if a target is known and a cache row for `(resolved_edition,
     resolved_version)` exists → return its `content` (no catalog download needed).
  3. **Fetch:** else, if a target is known and `catalog_auto_fetch`: download
     `community-<resolved_version>.json`; **verify** its SHA-256 == the manifest entry (reject + log on
     mismatch, do **not** cache); cache `content` + `sha256` under `(resolved_edition,
     resolved_version)`; return it.
  4. **Offline fallback:** if no target could be resolved (network down) or the catalog download
     failed, probe the cache directly for the device's own **exact** resolved identity if one was
     computed, else for `(edition, version)`. Return that `content` if present, else **None** ("catalog
     unavailable for this version"). A device previously resolved-and-cached is still served with the
     network down; a cold offline start, or an offline Business device that was never resolved, returns
     None.
- `async get_model(session, edition, version, model_id) -> dict | None` — convenience: the model from
  the catalog (or None).
- All HTTP via `httpx` with a timeout + `follow_redirects=True` (GitHub release downloads redirect).

Because Business resolves to a Community catalog, the warm cache is keyed by the **resolved** identity
(`community`, base version) — multiple BE versions sharing one base reuse a single cached row. The
offline fallback (step 4) is best-effort on the exact device identity; full offline resolution of a
*floored* or *Business-mapped* version is intentionally not supported (degrade to "unavailable").

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
`GET /api/tenants/{tid}/devices/{did}/catalog` →
`{edition, version, resolved_edition, resolved_version, models: [...] }` (the catalog for the device,
denylist-flagged). For a Business device `resolved_edition == "community"` and `resolved_version` is
its Community base — the UI can surface "showing the shared core (Community <base>) for this Business
device". Live values come from the device `get` at edit time (sub-project 3).

## Safety rails

- `CATALOG_DENYLIST` (code constant, v1): models that can lock the operator out of the box (interface
  assignment, and any flagged high-risk). The create endpoint refuses them (422); the read endpoint
  marks them `read_only: true`.
- The whole path still sits behind the default-OFF `LIVE_PUSH_ENABLED` master switch + the pre-apply
  snapshot + targeted revert + the staleness guard — unchanged.
- The applier never echoes raw config values into errors; the create endpoint is tenant-scoped (RLS).

## Testing

- **Provider:** pure `resolve_version` (exact/floor/none); pure `resolve_target` for community
  (passthrough) and business (BE version → Community base → manifest floor; unmapped BE → None);
  `get_catalog` fetch+verify (respx-mocked release: manifest + business-base + catalog) caches under
  the resolved identity; a Business device reuses the Community cache row; SHA-256 mismatch → rejected
  (not cached); offline with a cache row → served; offline cold → None.
- **Connector:** `apply_grid_item` add/set/del request shapes (respx); uuid charset guard; dry_run no-op.
- **Applier:** `catalog_setting` applies scalars + grids + one reconfigure; dry_run; unknown/denylist
  paths rejected.
- **API:** create resolves+validates+creates a draft (RLS app-role client); unknown model/denylist →
  422; no catalog for the device → 404; cross-tenant device → 404; read returns the device's catalog.
- **CLI:** `generate-all` emits N catalogs + a correct sha256 manifest (vendored mini source, no
  network); `business-base` parses a vendored `BE_*.html` fixture → the correct `map` (no network).

## Out of scope (this sub-project)

- The editor UI (sub-project 3) — forms from the catalog + live values + version-diff surfacing.
- Dynamic option resolution (interface/alias/CA lists) at edit time — sub-project 3 (the create
  endpoint accepts the chosen values; resolving the *choices* is the UI's job).
- **Business proprietary deltas** (plugins/features unique to BE, not in the Community base) — sub-project
  4, blocked on a Business box. Business devices are covered here only for the **shared core** via the
  Community-base catalog; the `business-base.json` map makes that resolution possible.
- A CI workflow that auto-publishes on new OPNsense tags (documented ops run for now); resolving `.\X`
  module-local field classes.
