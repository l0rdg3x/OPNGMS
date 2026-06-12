# OPNsense API config-schema catalog generator

Offline tool that turns OPNsense's tagged open source into a versioned JSON catalog of the
API-modifiable models (fields, types, options, endpoints, labels) + a cross-version diff. No device
needed. Sub-project 1 of the version/edition-aware generic config editor.

## Generate a full catalog for a version (network)

```bash
cd backend
.venv/bin/python -m tools.opnsense_catalog.cli generate \
    --edition community --version 26.1.8 --fetch --out ../catalog/community/26.1.8.json
```

`--fetch` downloads the `opnsense/core` tag tarball via codeload and extracts it. Omit `--fetch` and
pass `--source <dir>` to run over an already-extracted source tree. The coverage report
(`models`, `fields_total`, `fields_raw`) is printed after `generate`.

## Diff two versions

```bash
.venv/bin/python -m tools.opnsense_catalog.cli diff \
    ../catalog/community/26.1.7.json ../catalog/community/26.1.8.json
```

Prints added/removed models and per-model added/removed/changed fields — the "what changed between
versions" view (it also surfaces legacy settings newly migrated under MVC/API).

## Public plugins

Run the same `generate` with `--source` pointing at an extracted `opnsense/plugins` tree (fetch its
tarball the same way). Proprietary/Business plugins are not on public GitHub and are out of scope
here (covered later by a one-time box harvest).

## Coverage & the never-drop principle

The generator never drops a field: a field whose OPNsense class isn't in `model_parser._TYPE_MAP`
(or a model whose controller isn't MVC-standard) is emitted as `confidence:"raw"` (editable as text,
validated by the box) rather than omitted. When `generate` reports a high `fields_raw`, inspect which
field classes fell through and add them to `_TYPE_MAP` — coverage rises without ever blocking.

## Notes

- `id` / `model_root` (the API set-body root) come from the module directory (e.g. `unbound`); the
  `xml_path` (config.xml location) comes from the model `<mount>` and can differ (Unbound mounts at
  `OPNsense/unboundplus`).
- Dynamic option lists (e.g. "pick an interface/alias/CA") are NOT in the static catalog — they're
  fetched live from the device at edit time (sub-projects 2–3).
