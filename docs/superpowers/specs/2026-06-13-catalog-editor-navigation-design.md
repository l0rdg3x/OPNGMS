# Sub-project 3b — OPNsense-like navigation + live options — design

## Where this fits

The killer feature is a version/edition-aware OPNsense config editor. Sub-projects 1 (catalog
generator), 2 (distribution + apply engine, #94), the 6h publish Action (#96), and 3a (editor
foundation, #97) are done. 3a ships an **"Editor" tab** with a **flat searchable model list**. **3b
turns that flat list into the OPNsense-like console navigation** — the real left menu tree rebuilt
from OPNsense's `Menu.xml`, plus global search — and adds **live dynamic option lists** for `ref`
fields. 3c (next) adds cross-version diff badges + the read-only live `config.xml` map.

## Decisions taken with the user (2026-06-13)

- **Navigation fidelity = faithful menu, model-grained editing.** Rebuild OPNsense's exact left menu
  (Category → Module → pages) from `Menu.xml`. Clicking ANY menu entry opens that entry's **catalog
  model form** (3a) — OPNsense's finer page split (General/Overrides/Advanced) collapses to the one
  model. Entries that don't map to a catalog model (diagnostics, legacy, no model) are shown **greyed
  with a deep-link to the device WebGUI** (the menu's `url`).
- **3b includes dynamic `ref` options.** Insight: OPNsense renders `ModelRelationField`/`InterfaceField`
  in a model `get` response as an **option-dict** (`{key: {value: label, selected}}`) — the available
  choices are already in the **live read** 3a performs. So no new endpoint/connector: the live model
  endpoint also returns the available **options** per field; the editor renders `ref`/`enum` with live
  options as dropdowns. (To verify on a real device: confirm relation fields come back as option-dicts.)
- **Menu in the catalog JSON** (one fetch), not a separate asset. **Options reuse the live read**, not
  a dedicated endpoint. **Scope = core** (matches catalog coverage); plugins later.

## Part A — Generator: harvest `Menu.xml`

OPNsense menu fragments live at `mvc/app/models/OPNsense/<Module>/Menu/Menu.xml`. Each is a tree whose
**element tags** are ids and whose attributes are `VisibleName` (label, falls back to the tag), `order`,
`url` (a leaf), and `cssClass` (an icon). Multiple modules contribute under the same top-level category
(e.g. many under `<Services>`), so the fragments must be **merged by id-path**.

**New module `tools/opnsense_catalog/menu.py`:**
- `discover_menus(root) -> list[Path]` — all `**/mvc/app/models/OPNsense/*/Menu/Menu.xml`.
- `parse_menu(xml_text) -> list[Node]` — one fragment to a list of recursive nodes:
  `Node = {id, label, order, icon?, url?, children: [Node]}` (a node with `url` and no children is a
  **leaf/page**; `icon` from `cssClass`; `label` from `VisibleName` or the tag).
- `merge_menus(fragments) -> list[Node]` — deep-merge fragments by id-path (same id at the same level
  is one node; children union; a later `label`/`icon`/`url` fills a missing one, never overwrites).
  Sort siblings by `order` (missing → after, then by label).
- `resolve_model_ids(menu, model_ids) -> menu` — for each leaf, parse its `url` `/ui/<a>/<b>/...` and
  set `model_id` to the first of `"<a>.<b>"`, `"<a>"` that exists in the catalog's model ids, else
  `null` (un-editable → the editor greys it + WebGUI deep-links the `url`).

**Wire into generation** (`cli.py` `_generate` / `assemble`): after building the models, harvest +
merge + resolve, and set `catalog["menu"] = <merged resolved tree>`. The 6h publish Action republishes
this automatically (the core tarball it already fetches contains the `Menu.xml` files); no new asset,
no app release. `coverage_report` gains menu counts (categories, leaves, unmapped leaves) for visibility.

## Part B — Backend: live options on the model endpoint

The menu already rides in the catalog JSON, so `GET …/catalog` (3a) returns it unchanged. Only the
**live model endpoint** grows, to surface the available choices:

- `services/catalog_live.py` — add `extract_options(get_response, model) -> dict[str, list[dict]]`:
  for each scalar field whose live value is an **option-dict**, return `{path: [{value, label}]}` (reuse
  the shared `opnsense_values.options`). Grid-cell options: `extract_grid_options(get_response, model,
  grid)` similarly per `{grid_path: {field_path: [{value,label}]}}`.
- `GET …/catalog/models/{id}` response gains `field_options` (scalars) and `grid_field_options` (per
  grid). Empty when unreachable. `read_only`/`reachable`/`values`/`grids`/`model` unchanged from 3a.

No connector change, no new mutation surface; this is read-only data already fetched in 3a.

## Part C — Frontend: the menu tree + search + live dropdowns

**`CatalogEditorTab` (rework the left pane):**
- Replace the flat model list with a **menu tree** from `catalog.menu`: Category → Module → pages,
  with icons and `order` sorting (a recursive `CatalogMenuTree` using Mantine `NavLink` nesting).
- A **global search** box filters the tree: keep a branch if any descendant leaf's label/url — or its
  mapped model's `title`/field labels — matches; matched leaves highlighted. (Field-label matching uses
  the already-loaded `catalog.models[*].fields[*].label`.)
- Clicking a leaf with `model_id` selects that model → the right pane shows the 3a `CatalogModelForm`.
  A leaf with `model_id == null` renders greyed; clicking it opens the device WebGUI at the leaf `url`
  in a new tab (the device base_url + `url`), like the existing "WebGUI deep-link" button.

**`CatalogFieldInput` (live options):** accept an optional `liveOptions?: {value,label}[]`. When present
and the field is `ref` **or** `enum`, render a `Select`/`MultiSelect` from `liveOptions` (preferring
live over the catalog's static `enum` options); a `ref` with no live options falls back to the current
text input. `CatalogModelForm` threads `field_options[path]` into each input, and `grid_field_options`
into `CatalogGridTable`'s row modal.

## Safety / security

- All new data is **read-only** and tenant-scoped: the menu is static catalog content; `field_options`
  come from the same live `get` 3a already does (DEVICE_VIEW, ownership-guarded, degrades on error).
- No new mutation path. Proposing still goes through the unchanged `POST …/catalog/changes`
  (CONFIG_PUSH + CSRF + denylist + `LIVE_PUSH_ENABLED`).
- The WebGUI deep-link uses the device `base_url` (already shown elsewhere) + the menu `url`; the `url`
  is catalog content (SHA-256-verified), opened in a new tab — no injection into our own routes.

## Testing

- **Generator:** `parse_menu` (tags→ids, VisibleName/cssClass/order/url, nesting); `merge_menus` (two
  fragments under the same category merge, sibling order, no overwrite); `resolve_model_ids` (`/ui/ids`
  →`ids`, `/ui/ids/policy`→`ids`, `/ui/diagnostics/log/...`→null); end-to-end `generate` emits
  `catalog["menu"]` with resolved leaves (vendored mini source gains a `Menu/Menu.xml`).
- **Backend:** `extract_options`/`extract_grid_options` (option-dict→choices, non-option→absent);
  model endpoint returns `field_options`/`grid_field_options` when reachable, empty when not.
- **Frontend:** `CatalogMenuTree` renders categories→modules→pages and filters on search; an unmapped
  leaf is greyed + deep-links; `CatalogFieldInput` renders a Select from `liveOptions` for a `ref` and
  falls back to text without; `CatalogModelForm` threads options into inputs + grid modal.

## Out of scope (this stage)

- Per-page field parity (each menu page showing only its subset of fields) — the chosen fidelity opens
  the whole model form per entry.
- Plugin menus/models (core only here).
- Cross-version **diff badges** + the read-only live **`config.xml` map** — **3c**.
- Resolving option lists **offline** (no live read) — options come from the live `get`; an unreachable
  device shows `ref` as text (editing is already disabled when unreachable, per 3a).
