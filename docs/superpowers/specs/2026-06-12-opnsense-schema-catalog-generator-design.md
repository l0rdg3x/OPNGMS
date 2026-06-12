# OPNsense API config-schema catalog generator (sub-project 1) — design

## Where this fits (the bigger program)

The killer feature is a **version/edition-aware generic OPNsense config editor**: navigate and edit
**every API-modifiable setting** for a device's exact OPNsense version, see what changed between
versions, and push via the existing config pipeline. That is a multi-milestone program:

1. **Catalog generator** — *this spec*. An offline tool that turns OPNsense's open source (per
   release tag) into a versioned JSON catalog of API-modifiable models + a cross-version diff. No
   running app, no device.
2. Generic apply engine — generalize `opnsense_setting` from a curated list to catalog-driven
   (scalars + grids + dynamic options), with safety rails.
3. Editor UI — an OPNsense-like navigable form rendered from the catalog + live values.
4. Business proprietary delta (later, via a one-time box harvest).

This spec covers **only sub-project 1**.

## The API-surface reality (why this approach)

OPNsense has **no** universal "write any `config.xml` node" API. The API-writable surface is the
union of **MVC model modules**: each exposes `…/settings/get` + `…/settings/set` (+
`searchItem/addItem/setItem/delItem` for grid/array nodes) + `…/service/reconfigure`. A small set of
legacy core settings have no MVC `set` (uncoverable by *any* API tool — an OPNsense limit, **not**
ours); that set shrinks every release as OPNsense migrates legacy → MVC, so our coverage grows for
free and the version-diff will surface those migrations.

Everything needed to describe these models is **open source and tagged**:

- **Core** `github.com/opnsense/core@<tag>`: models at
  `src/opnsense/mvc/app/models/OPNsense/<Module>/<Model>.xml` (field classes + `Required` /
  `Multiple` / `OptionValues` / constraints / defaults), forms at
  `src/opnsense/mvc/app/views/OPNsense/<Module>/forms/*.xml` (+ `.volt`) for labels/help/page
  grouping, API controllers at `src/opnsense/mvc/app/controllers/OPNsense/<Module>/Api/*.php` for
  endpoint paths.
- **Public plugins** `github.com/opnsense/plugins`: same structure per plugin.

**The catalog is 100% offline (GitHub).** Two things are still read **from the device, but only at
edit time** (sub-projects 2–3, not here): current values (the model `get`) and **dynamic option
lists** (e.g. "pick an interface/alias/CA" — populated at runtime, not in the static model).

## Goal (this sub-project)

A reproducible generator: given `(edition, version_tag)`, fetch the tagged source and emit a
**complete** catalog JSON of every API-modifiable model (core, then public plugins), and a pure
**diff** between two catalogs. Definition of done = **complete core coverage** for a version (plus
public plugins), not a sample.

## Core principle: **never-drop** (this is what makes it "everything")

The generator **never silently omits** a model or field. Coverage is total by construction, with a
*quality gradient*:

- field with a recognised class → **rich** schema (typed widget, options, validators);
- field with an unknown class, or an endpoint not resolvable by convention → emitted anyway as
  **`raw`** (string, editable as text, validated server-side by OPNsense at `set`), flagged
  `confidence: "raw"` so the UI/diff and our own coverage reports can see it.

So the catalog is complete; fidelity improves over time by refining mappings, without ever blocking
coverage. This folds the "edit-anything-validated-by-the-box" fallback *into* the catalog, per field.

## Architecture (small, single-purpose, testable units)

A standalone generator under `backend/tools/opnsense_catalog/` (offline tooling, **not** imported by
the running app). Emitted catalogs are committed as data under `catalog/<edition>/<version>.json`;
the app later loads them read-only.

1. **Fetcher** `fetch.py` — `fetch_source(repo, ref) -> Path`. Downloads the **tag tarball** via
   codeload (`https://codeload.github.com/opnsense/<repo>/tar.gz/refs/tags/<tag>` for core; a
   branch/ref for plugins), extracts only the `models` / `forms` / `Api` subtrees to a temp dir. No
   git, no auth. Network only here; everything downstream is pure over files.
2. **Model parser** `model_parser.py` — `parse_model(xml_text) -> Model`. Walks `<Model>.xml` into a
   field tree; maps OPNsense field classes → catalog types:
   `BooleanField→bool`, `OptionField(+OptionValues, Multiple)→enum|multienum (static options)`,
   `Text/Description/HostField→string`, `IntegerField→int`, `NetworkField/EmailField/PortField→typed
   string`, `ModelRelationField/*ConstraintField→ref` (a reference whose options are resolved live).
   Captures `Required`, `Multiple` (→ a **grid** node), `<default>`, `Mask`/`ValidationMessage`.
   Unknown class → `string` + `confidence:"raw"`.
3. **Form parser** `form_parser.py` — `parse_forms(xml_texts) -> {field_id: {label, help, page}}`.
   Best-effort labels/help/tab grouping; missing → label derived from the field id.
4. **Endpoint resolver** `endpoints.py` — `resolve_endpoints(module, model, controller_php) -> Endpoints`.
   Convention first (`<module>/settings/get|set`, `<module>/service/reconfigure`; grids →
   `search/add/set/del<Item>`), then parse the `*ApiController` PHP to confirm the bound model /
   override non-standard routes. Unresolved → `confidence:"raw"`, **never dropped**.
5. **Emitter** `emit.py` — `build_catalog(models, forms, endpoints, *, edition, version) -> dict`
   with **stable key ordering** (sorted) so file diffs are clean. Writes
   `catalog/<edition>/<version>.json`. Each model records its **provenance** (`source: "core" | a
   plugin name + plugin version`) so the differ can attribute changes.
6. **Differ** `diff.py` — `diff_catalogs(a, b) -> CatalogDiff` (pure): `added_models`,
   `removed_models`, and per shared model `added_fields` / `removed_fields` /
   `changed_fields:[{path, attr, before, after}]`.

## Catalog JSON shape (concrete)

```json
{
  "edition": "community",
  "version": "26.1.8",
  "generated_from": {"core": "26.1.8", "plugins_ref": "stable/26.1"},
  "models": {
    "ids.general": {
      "title": "Intrusion Detection — General",
      "source": "core",
      "model_root": "ids",
      "xml_path": "OPNsense/IDS",
      "endpoints": {"get": "ids/settings/get", "set": "ids/settings/set",
                    "reconfigure": "ids/service/reconfigure"},
      "pages": [{"id": "general", "label": "General", "fields": ["general.enabled", "general.homenet"]}],
      "fields": [
        {"path": "general.enabled", "type": "bool", "required": false, "default": "0",
         "label": "Enabled", "help": "...", "confidence": "rich"},
        {"path": "general.homenet", "type": "multienum", "options": ["..."], "label": "Home networks",
         "confidence": "rich"}
      ],
      "grids": [
        {"path": "userDefinedRules", "endpoints": {"search": "ids/settings/searchUserRuleset",
          "add": "ids/settings/addUserRuleset", "set": "ids/settings/setUserRuleset",
          "del": "ids/settings/delUserRuleset"}, "fields": ["..."]}
      ]
    }
  }
}
```

## Scope

**In:** complete **core** model coverage for a version (all `Model.xml` under the core models tree),
then **public plugins** (same pipeline over `opnsense/plugins`); the version-diff; the never-drop
raw fallback; provenance per model.

**Out (this sub-project):** the apply engine (sub-2), the editor UI (sub-3), **dynamic option
resolution** (edit-time, needs the device), **proprietary/Business plugins** (not on public GitHub —
deferred to the box-harvest delta), and a running-app catalog loader (a thin reader comes with
sub-2).

## Build order (TDD)

Harden the engine on **1 model** (`ids.general` — we already know it via `opnsense_setting`, so we
validate the emitted schema against reality), then **3 models** (add e.g. `unbound.general` +
`monit` or the firewall alias model), then run across the **whole core tree**, then **public
plugins**. Emit + diff two real tags (**26.1.7 → 26.1.8**) to prove the version-diff.

## Error handling

- Unknown field class / unresolved endpoint → `confidence:"raw"`, **emitted, never dropped**; the
  emitter also writes a **coverage report** (counts of rich vs raw per module) so completeness is
  measurable, not assumed.
- Malformed/missing form file → fall back to id-derived labels (model parsing still succeeds).
- Fetcher network/extract failure → raise with the repo+ref; the generator is re-runnable.

## Testing

- **Vendored fixtures**: real `Model.xml` / `forms/*.xml` / controller PHP snippets pinned from the
  tags, committed under `tests/fixtures/opnsense_catalog/`. Parser/endpoint/emitter tests assert the
  emitted schema against **golden** JSON (no network in CI).
- **Differ** unit tests over two hand-built catalogs (add/remove/change cases).
- One **integration** test guarded behind a marker that actually fetches a pinned tag and emits
  `ids.general`, matching the golden (skipped by default / network-gated).

## Open considerations (handled, noted for the plan)

- **Plugin version independence**: plugins version separately from the core release; v1 harvests the
  plugins repo at the ref matching the core series and records each model's plugin version in
  `source`, so the differ attributes plugin changes correctly.
- **Regeneration cadence**: a later periodic job watches new core/plugin tags and regenerates +
  commits catalogs; out of scope to *build* the watcher here, but the generator is the unit it will
  call.
