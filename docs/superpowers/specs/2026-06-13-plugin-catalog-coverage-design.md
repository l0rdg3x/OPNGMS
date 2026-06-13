# Plugin coverage & lifecycle — design

**Status:** approved (2026-06-13). One unified design, built in four sequential phases (one PR per phase).

## Goal

Extend the version-aware OPNsense config system so it covers **community plugins**, not just
`opnsense/core`. From OPNGMS an operator can **install/remove a plugin on a managed box** and then
**manage the configuration** of the installed plugins, through the same editor/template machinery that
already serves core models.

## Why now / what makes it non-trivial

The catalog generator (`backend/tools/opnsense_catalog/`) harvests `opnsense/core` per release tag and
publishes a per-version JSON catalog. Plugins live in a **separate repo** (`opnsense/plugins`) with
their own MVC models and `Menu.xml` fragments. The model-parser, menu-merge, and the generic
`catalog_setting` apply kind already work on any MVC model — so the hard parts are sourcing/versioning
the plugin catalog, persisting per-device install state, and adding a gated install/remove action.

## Grounding facts (verified 2026-06-13)

- **`opnsense/plugins` is tagged 1:1 with core** (`26.1.9`, `26.1.8`, …). The plugin catalog therefore
  tracks the **same version** as the core catalog — no separate versioning scheme is needed.
- **Layout:** `<category>/<plugin>/` (categories: `net`, `security`, `sysutils`, `dns`, `www`,
  `net-mgmt`, `devel`, `databases`, `mail`, …). Each plugin dir has a `Makefile` with
  `PLUGIN_NAME=` (→ package `os-<name>`), `PLUGIN_VERSION=`, `PLUGIN_COMMENT=` (human title), and a
  `src/opnsense/mvc/app/models/OPNsense/<Module>/…` tree. Non-plugin dirs (`Mk`, `Scripts`,
  `Templates`, `Keywords`, `vendor`, `.github`) must be skipped.
- **Some plugins have no MVC models** (legacy/theme/etc.) — installable but **not** config-editable.
- **The box is authoritative** for plugin install state: `core/firmware/info` returns *all available*
  plugins with an `installed` flag + version. OPNGMS already parses this (`parsers.parse_plugins`,
  `profiles.plugin_info`) but keeps only installed names and **does not persist** them.
- The `catalog_setting` apply kind (`backend/app/services/catalog_kind.py`) is **fully generic** —
  endpoints + `model_root` resolved at proposal time. It already works for any model id, core or plugin.
- Catalog consumer: `catalog_provider.get_catalog(session, edition, version)` fetches
  `<base>/<edition>-<version>.json`, verifies SHA-256 against `manifest.json`, caches in the
  `catalog_cache` Postgres table. Business devices map to a Community base via `business-base.json`.

## Architecture overview

Four phases, each independently mergeable and individually valuable. They build on each other; a single
coherent design ensures the Phase-1 catalog already carries the metadata (package name) that the
Phase-3 install action needs.

```
Phase 1  Plugin catalog        generator → community-plugins-<ver>.json (separate asset) → consumer merge
Phase 2  Install telemetry     persist the box's plugin list (installed + version) per device, on poll
Phase 3  Lifecycle action      install/remove os-<name> via firmware API, gated by the apply pipeline
Phase 4  UI                    per-device "Plugins" page (lifecycle) + plugin models badged in the editor
```

---

## Phase 1 — Plugin catalog (separate per-version asset)

**Packaging decision:** a **separate** `community-plugins-<version>.json` asset published alongside
`community-<version>.json` (NOT merged into the core catalog). Keeps the proven core path untouched,
makes plugins optional/lazy, keeps the incremental publish clean, and isolates a plugin parse failure
from the core catalog. The app loads both and merges the menus at runtime.

**Generator (`backend/tools/opnsense_catalog/`):**

- `fetch.py` — already generic: `fetch_source("plugins", "<version>", dest)` →
  `https://codeload.github.com/opnsense/plugins/tar.gz/refs/tags/<version>`. No change needed.
- `discover.py` — extend discovery to the plugins layout: for each `<category>/<plugin>/` dir that has a
  `Makefile` defining `PLUGIN_NAME`, descend its `src/opnsense/mvc/app/models/…` tree (reuse the
  existing model/form/controller discovery). Skip non-plugin top-level dirs. Capture, per discovered
  model, its owning plugin's `{package: "os-<PLUGIN_NAME>", title: <PLUGIN_COMMENT>, category, version:
  <PLUGIN_VERSION>}`.
- `types.py` / `emit.py` — add an optional `plugin` block to `Model` and emit it:
  `plugin: {package, title, category, version}`. Core models keep `source:"core"` and no `plugin` block;
  plugin models get `source:"plugins"`.
- `cli.py` — a `generate-plugins` path (or `generate`/`generate-all` gaining `--repo {core,plugins}`),
  writing `community-plugins-<version>.json` with `generated_from={"plugins": version}`. The menu is
  built from the plugins' `Menu.xml` fragments (the existing `discover_menus`/`merge_menus` already
  globs `**/mvc/app/models/OPNsense/*/Menu/Menu.xml`, which matches the plugin tree).
- Coverage report + the **never-drop** principle unchanged (unknown field classes → `confidence:"raw"`).

**Publish (`.github/workflows/publish-catalogs.yml`):** after the core catalogs, generate plugin
catalogs for the same versions (incremental, carrying already-published versions from the prior
manifest), and upload `community-plugins-<version>.json`. The manifest gains
`community-plugins/<version>` → sha256 entries.

**Consumer (`backend/app/services/catalog_provider.py`):** new
`get_plugins_catalog(session, edition, version)` mirroring `get_catalog` (same version resolution,
SHA-256 verification, `catalog_cache` PG caching, offline fallback). A small backend port of
`merge_menus` produces the unified editor menu (core menu ∪ plugins menu) so plugin entries appear in
their natural categories (e.g. *Services → HAProxy*).

**Deliverable:** plugin models are fetchable as a catalog and editable via the existing
`catalog_setting` path, exactly like core models.

---

## Phase 2 — Per-device plugin telemetry

Persist what the box reports so the UI can badge install state and the editor can gate not-installed
plugins.

- Extend `parsers.parse_plugins` to keep **all available** plugins with `{name, installed, version,
  locked}` (today it keeps only installed names). `name` is the `os-<…>` package id.
- Persist on the device as a JSONB column `device.installed_plugins: list[{name, installed, version,
  locked}]` (chosen over a dedicated table: the list is small, no history needed — YAGNI; revisit a
  table only if we later want per-plugin history). A forward-only Alembic migration adds the column.
- `backend/app/services/monitoring.py` `collect_and_store` calls the existing `plugin_info` capability
  and writes the column each poll. Tenant-scoped via the device row's RLS.
- Expose via the device API for the Plugins page + editor badges.

---

## Phase 3 — Plugin lifecycle action (install / remove), gated

- **Connector** (`OpnsenseClient` + `connectors/opnsense/profiles.py`/`endpoints.py`): add
  `install_plugin(name)` and `remove_plugin(name)` posting to the OPNsense firmware API
  (`core/firmware/install` / `core/firmware/remove` with `{name: "os-<…>"}`), plus `plugin_status()`
  reading `core/firmware/upgradestatus` to follow the async pkg operation to completion.
- **Apply pipeline:** a new change kind **`plugin_lifecycle`** (`{op: install|remove, name}`) registered
  like `catalog_setting`. It flows through the **existing** gated path: per-device master switch,
  scheduled action, audit log, the SSRF-guarded client (invariants #1, #4). A pkg install can't be
  meaningfully dry-run on the box, so dry-run validates the plugin exists (in the plugins catalog or the
  box's available list) and reports the intent.
- On completion, **re-poll** `firmware/info` to refresh the Phase-2 telemetry so the UI reflects the new
  state. The operation can be slow → it runs in the worker like other scheduled actions.

---

## Phase 4 — UI: Plugins page + editor integration

- **Per-device "Plugins" page:** lists plugins from the **box telemetry** (authoritative for install
  state + version), grouped by category, each row showing title, package, an **installed badge +
  version**, and **Install / Remove** buttons (→ the `plugin_lifecycle` apply flow). Cross-references the
  plugins catalog: plugins that have config models show a **"Configure"** link into the editor.
  Search/filter. Lives in the existing per-device navigation.
- **Editor:** plugin models appear in the merged menu, **badged installed / not-installed**. Opening an
  installed plugin edits its config via the existing `catalog_setting` path; a not-installed plugin is
  read-only with an "Install first" affordance linking to the Plugins page.
- **i18n:** new UI keys added to `en.ts` first, then mirrored across all 12 locales (compiler-enforced
  parity).

---

## Cross-cutting concerns

- **Security / invariants:** every device write (install/remove and config apply) goes through the
  SSRF-guarded client + apply pipeline + per-device master switch + audit. Telemetry and actions are
  tenant-scoped (RLS). No secret is returned or logged. (Invariants #1–#5 intact.)
- **Edition/Business:** plugin catalogs are Community-sourced; Business devices reuse the Community
  plugins catalog for their mapped base version, exactly as they do for the core catalog. Proprietary
  Business-only plugins are out of scope (see below).
- **Testing per phase:** generator unit tests over a small vendored plugin-tree fixture (→ models with
  the `plugin` block + correct `source`); consumer tests (fetch + SHA + menu merge); telemetry
  parse/persist tests; apply-kind tests (install/remove payloads + dry-run validation); frontend tests
  (Plugins page renders, badges, install button triggers the apply flow). Backend needs a live
  TimescaleDB; frontend gate is `npm run build`.

## Out of scope (deferred — parked as possible future TODOs)

- **Proprietary / Business-only plugins** — not on public GitHub; would need a one-time box harvest.
- **Fleet/template-level plugin install** — a config template that *ensures a plugin is installed*
  across many boxes before pushing its config. This milestone is per-device install; the template
  "ensure plugin" affordance is a future extension.
- **Package rollback / pinning** — no reliable OPNsense API to revert a pkg operation.
- **Non-MVC plugins' settings** — installable via the Plugins page, but they have no config models, so
  no editor section.

## File map (informational — the implementation plan will detail tasks)

| Area | Files |
|------|-------|
| Generator | `backend/tools/opnsense_catalog/{discover,types,emit,cli}.py` (+ `fetch.py` unchanged) |
| Publish | `.github/workflows/publish-catalogs.yml` |
| Consumer | `backend/app/services/catalog_provider.py` (+ a small runtime menu-merge helper) |
| Telemetry | `backend/app/connectors/opnsense/parsers.py`, `backend/app/services/monitoring.py`, `backend/app/models/device.py`, a new Alembic migration |
| Lifecycle | `backend/app/connectors/opnsense/{profiles,endpoints}.py`, `OpnsenseClient`, a new `plugin_lifecycle` change kind, the device API |
| UI | a per-device Plugins page + editor badge integration under `frontend/src/`, plus `i18n/en.ts` and the 12 locale dictionaries |
