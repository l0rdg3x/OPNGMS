# Plugin Catalog Coverage — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate, publish, and fetch a per-version OPNsense **plugins** catalog (`community-plugins-<ver>.json`) so the existing version-aware config machinery can target plugin models — the foundation for the later phases.

**Architecture:** The model/form/endpoint parsers and the generic `catalog_setting` apply kind already work on any MVC model. This phase (a) teaches the offline generator to harvest `opnsense/plugins` — pairing each discovered model with its owning plugin's `Makefile` metadata and tagging it `source:"plugins"` with a `plugin:{package,title,category,version}` block; (b) extends `generate-all` with `--with-plugins` so each core version ALSO emits a separate plugins catalog asset into the one shared manifest; (c) wires the publish workflow; and (d) adds a `get_plugins_catalog` provider that fetches + SHA-verifies + caches it exactly like the core catalog.

**Tech Stack:** Python 3.14, the `tools.opnsense_catalog` generator, `httpx`/`respx`, pytest (asyncio auto), SQLAlchemy async + the `catalog_cache` table, GitHub Actions.

**Scope note:** Phases 2–4 (per-device install telemetry, the gated install/remove apply kind, and the Plugins page + editor menu-merge/badges) get their OWN plans after each predecessor lands — their exact shape depends on Phase 1's real output. Phase 1 stops at "plugin catalogs are generated, published, and fetchable+cached by the backend".

**Branch:** `feat/plugin-catalog-coverage` (already created; the design spec is committed there).

**Spec:** `docs/superpowers/specs/2026-06-13-plugin-catalog-coverage-design.md`.

**Run all backend commands from `backend/` with the venv active** (`. .venv/bin/activate`). Tests need a reachable TimescaleDB (`ADMIN_DATABASE_URL`/`TEST_DATABASE_URL` per AGENTS.md). Lint gate: `ruff check app/ tools/`.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `backend/tools/opnsense_catalog/discover.py` | Find MVC models; NEW: find plugin dirs + parse their Makefile, pair each plugin's models with its metadata | Modify |
| `backend/tools/opnsense_catalog/types.py` | `Model` dataclass + `model_to_dict`; NEW optional `plugin` block | Modify |
| `backend/tools/opnsense_catalog/emit.py` | `assemble_model`; NEW `plugin` passthrough | Modify |
| `backend/tools/opnsense_catalog/cli.py` | `_generate`/`_write_catalog`/`generate-all`; NEW `--with-plugins`, plugins repo path, dual-key manifest | Modify |
| `backend/app/services/catalog_provider.py` | NEW `get_plugins_catalog` (fetch+SHA+cache the plugins asset) | Modify |
| `.github/workflows/publish-catalogs.yml` | NEW: pass `--with-plugins` to the generate step | Modify |
| `backend/tests/fixtures/opnsense_catalog/miniplugins/**` | NEW fixture: a 2-plugin tree mirroring `opnsense/plugins` layout | Create |
| `backend/tests/test_catalog_plugins_discover.py` | Tests for Makefile parse + plugin discovery | Create |
| `backend/tests/test_catalog_emit.py` | Existing emit tests + NEW `plugin` passthrough/emit assertions | Modify |
| `backend/tests/test_catalog_cli.py` | Existing CLI tests + NEW `--with-plugins` generate-all assertions | Modify |
| `backend/tests/test_catalog_provider_plugins.py` | Tests for `get_plugins_catalog` | Create |

---

## Task 1: Parse a plugin Makefile

**Files:**
- Modify: `backend/tools/opnsense_catalog/discover.py`
- Test: `backend/tests/test_catalog_plugins_discover.py` (create)

OPNsense plugin Makefiles declare `PLUGIN_NAME`, `PLUGIN_VERSION`, `PLUGIN_COMMENT` with tab/space-padded `=` assignments (e.g. `PLUGIN_NAME=\t\thaproxy`). The package id is `os-<PLUGIN_NAME>`. Non-plugin Makefiles (`Mk/`, `Templates/`, `vendor/`) have no `PLUGIN_NAME` and must yield `{}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_catalog_plugins_discover.py`:

```python
from pathlib import Path

from tools.opnsense_catalog.discover import discover_plugin_models, parse_plugin_makefile


def test_parse_plugin_makefile_extracts_name_version_comment():
    text = (
        "PLUGIN_NAME=\t\thaproxy\n"
        "PLUGIN_VERSION=\t\t5.1\n"
        "PLUGIN_COMMENT=\t\tReliable, high performance TCP/HTTP load balancer\n"
        "PLUGIN_DEPENDS=\t\thaproxy\n"
    )
    meta = parse_plugin_makefile(text)
    assert meta == {
        "name": "haproxy",
        "version": "5.1",
        "comment": "Reliable, high performance TCP/HTTP load balancer",
    }


def test_parse_plugin_makefile_without_plugin_name_is_empty():
    # A framework Makefile (Mk/, Templates/) has no PLUGIN_NAME -> not a plugin.
    assert parse_plugin_makefile("CORE_NAME=\topnsense\nall:\n\techo hi\n") == {}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_catalog_plugins_discover.py::test_parse_plugin_makefile_extracts_name_version_comment -q`
Expected: FAIL — `ImportError: cannot import name 'parse_plugin_makefile'`.

- [ ] **Step 3: Implement `parse_plugin_makefile`**

In `backend/tools/opnsense_catalog/discover.py`, add `import re` at the top (after `from pathlib import Path`) and append:

```python
_MK_VAR = re.compile(r"^(PLUGIN_NAME|PLUGIN_VERSION|PLUGIN_COMMENT)\s*[+:]?=\s*(.+?)\s*$", re.M)
_MK_KEY = {"PLUGIN_NAME": "name", "PLUGIN_VERSION": "version", "PLUGIN_COMMENT": "comment"}


def parse_plugin_makefile(text: str) -> dict:
    """Extract {name, version, comment} from a plugin Makefile. Empty dict if it defines no
    PLUGIN_NAME (i.e. it is a framework/non-plugin Makefile)."""
    out: dict[str, str] = {}
    for m in _MK_VAR.finditer(text):
        out[_MK_KEY[m.group(1)]] = m.group(2).strip()
    return out if out.get("name") else {}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_catalog_plugins_discover.py -q`
Expected: PASS (1 of the 2 will still fail on the missing `discover_plugin_models` import — that is Task 2; run just the makefile test: `python -m pytest "tests/test_catalog_plugins_discover.py::test_parse_plugin_makefile_extracts_name_version_comment" "tests/test_catalog_plugins_discover.py::test_parse_plugin_makefile_without_plugin_name_is_empty" -q`). Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tools/opnsense_catalog/discover.py backend/tests/test_catalog_plugins_discover.py
git commit -m "feat(catalog): parse plugin Makefile metadata (name/version/comment)"
```

---

## Task 2: Discover plugin models paired with their plugin metadata

**Files:**
- Modify: `backend/tools/opnsense_catalog/discover.py`
- Create fixture: `backend/tests/fixtures/opnsense_catalog/miniplugins/**`
- Test: `backend/tests/test_catalog_plugins_discover.py`

`discover_models(root)` already rglobs `mvc/app/models/OPNsense/*/*.xml`, which finds plugin models too — but it doesn't know which plugin owns each model. Add `discover_plugin_models(root)`: for every `<category>/<plugin>/Makefile` that defines `PLUGIN_NAME`, run the existing `discover_models` over that plugin's directory and tag each result with `PluginMeta`.

- [ ] **Step 1: Create the fixture tree**

Create these files (a 2-plugin tree: `net/haproxy` with an MVC model, and `devel/notinstalled` with one too; plus a framework `Mk/plugins.mk` with no PLUGIN_NAME to prove it is skipped):

`backend/tests/fixtures/opnsense_catalog/miniplugins/net/haproxy/Makefile`:
```make
PLUGIN_NAME=		haproxy
PLUGIN_VERSION=		5.1
PLUGIN_COMMENT=		Reliable, high performance TCP/HTTP load balancer
```

`backend/tests/fixtures/opnsense_catalog/miniplugins/net/haproxy/src/opnsense/mvc/app/models/OPNsense/HAProxy/HAProxy.xml`:
```xml
<model><mount>//OPNsense/HAProxy/general</mount><items><general><enabled type="BooleanField"/></general></items></model>
```

`backend/tests/fixtures/opnsense_catalog/miniplugins/devel/widget/Makefile`:
```make
PLUGIN_NAME=		widget
PLUGIN_VERSION=		1.0
PLUGIN_COMMENT=		A devel widget
```

`backend/tests/fixtures/opnsense_catalog/miniplugins/devel/widget/src/opnsense/mvc/app/models/OPNsense/Widget/Widget.xml`:
```xml
<model><mount>//OPNsense/Widget/general</mount><items><general><enabled type="BooleanField"/></general></items></model>
```

`backend/tests/fixtures/opnsense_catalog/miniplugins/Mk/plugins.mk`:
```make
# framework include, not a plugin
LOCALBASE?=	/usr/local
```

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_catalog_plugins_discover.py`:

```python
_MINI = Path(__file__).parent / "fixtures" / "opnsense_catalog" / "miniplugins"


def test_discover_plugin_models_pairs_each_model_with_its_plugin():
    found = discover_plugin_models(_MINI)
    by_pkg = {pms.plugin.package: pms for pms in found}
    assert set(by_pkg) == {"os-haproxy", "os-widget"}

    hap = by_pkg["os-haproxy"]
    assert hap.plugin.title == "Reliable, high performance TCP/HTTP load balancer"
    assert hap.plugin.category == "net"
    assert hap.plugin.version == "5.1"
    assert hap.source.module == "HAProxy"
    assert hap.source.model_xml.endswith("HAProxy/HAProxy.xml")

    # The framework Makefile under Mk/ contributes no plugin/model.
    assert all(pms.plugin.category != "Mk" for pms in found)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_catalog_plugins_discover.py::test_discover_plugin_models_pairs_each_model_with_its_plugin -q`
Expected: FAIL — `ImportError: cannot import name 'discover_plugin_models'`.

- [ ] **Step 4: Implement discovery**

In `backend/tools/opnsense_catalog/discover.py`, add to the imports `from dataclasses import dataclass, field` already exists; append after `discover_models`:

```python
@dataclass
class PluginMeta:
    package: str        # "os-haproxy"
    title: str          # PLUGIN_COMMENT
    category: str       # owning top-level dir, e.g. "net"
    version: str        # PLUGIN_VERSION


@dataclass
class PluginModelSource:
    source: ModelSource
    plugin: PluginMeta


def discover_plugin_models(root: Path) -> list[PluginModelSource]:
    """Find every `<category>/<plugin>/Makefile` that defines PLUGIN_NAME and pair each of that
    plugin's MVC models (via the core `discover_models`) with its `PluginMeta`. Plugins without MVC
    models contribute nothing (installable but not config-editable)."""
    out: list[PluginModelSource] = []
    for makefile in sorted(root.rglob("Makefile")):
        meta = parse_plugin_makefile(makefile.read_text())
        if not meta:
            continue
        plugin_dir = makefile.parent
        category = plugin_dir.parent.name
        plugin = PluginMeta(
            package=f"os-{meta['name']}",
            title=meta.get("comment", ""),
            category=category,
            version=meta.get("version", ""),
        )
        for src in discover_models(plugin_dir):
            out.append(PluginModelSource(source=src, plugin=plugin))
    return out
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_catalog_plugins_discover.py -q`
Expected: PASS (all 3 tests).

- [ ] **Step 6: Commit**

```bash
git add backend/tools/opnsense_catalog/discover.py backend/tests/test_catalog_plugins_discover.py backend/tests/fixtures/opnsense_catalog/miniplugins
git commit -m "feat(catalog): discover plugin models paired with plugin metadata"
```

---

## Task 3: Add an optional `plugin` block to the catalog Model

**Files:**
- Modify: `backend/tools/opnsense_catalog/types.py`
- Test: `backend/tests/test_catalog_types.py` (add a case)

The emitted model gains an optional `plugin` dict; core models omit it.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_catalog_types.py`:

```python
from tools.opnsense_catalog.types import Model, model_to_dict


def test_model_to_dict_emits_plugin_block_when_present():
    core = model_to_dict(Model(id="ids", title="IDS", source="core", model_root="ids",
                               xml_path="OPNsense/IDS"))
    assert "plugin" not in core  # core models carry no plugin block

    plug = model_to_dict(Model(id="haproxy", title="HAProxy", source="plugins",
                               model_root="haproxy", xml_path="OPNsense/HAProxy/general",
                               plugin={"package": "os-haproxy", "title": "HAProxy",
                                       "category": "net", "version": "5.1"}))
    assert plug["source"] == "plugins"
    assert plug["plugin"] == {"package": "os-haproxy", "title": "HAProxy",
                              "category": "net", "version": "5.1"}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_catalog_types.py::test_model_to_dict_emits_plugin_block_when_present -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'plugin'`.

- [ ] **Step 3: Implement the field + emission**

In `backend/tools/opnsense_catalog/types.py`, add the field to `Model` (after `pages`):

```python
@dataclass
class Model:
    id: str
    title: str
    source: str
    model_root: str
    xml_path: str
    endpoints: dict[str, str] = field(default_factory=dict)
    fields: list[Field] = field(default_factory=list)
    grids: list[Grid] = field(default_factory=list)
    pages: list[dict] = field(default_factory=list)
    plugin: dict | None = None          # {package, title, category, version} for source=="plugins"
```

And in `model_to_dict`, append the `plugin` key when set (after building the base dict, before `return`):

```python
def model_to_dict(m: Model) -> dict:
    out = {
        "id": m.id, "title": m.title, "source": m.source, "model_root": m.model_root,
        "xml_path": m.xml_path,
        "endpoints": dict(sorted(m.endpoints.items())),
        "pages": m.pages,
        "fields": [_field_to_dict(f) for f in sorted(m.fields, key=lambda f: f.path)],
        "grids": [_grid_to_dict(g) for g in sorted(m.grids, key=lambda g: g.path)],
    }
    if m.plugin is not None:
        out["plugin"] = m.plugin
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_catalog_types.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/tools/opnsense_catalog/types.py backend/tests/test_catalog_types.py
git commit -m "feat(catalog): optional plugin block on the catalog Model"
```

---

## Task 4: Thread `plugin` through `assemble_model`

**Files:**
- Modify: `backend/tools/opnsense_catalog/emit.py`
- Test: `backend/tests/test_catalog_emit.py` (add a case)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_catalog_emit.py`:

```python
def test_assemble_model_attaches_plugin_block():
    plugin = {"package": "os-haproxy", "title": "HAProxy", "category": "net", "version": "5.1"}
    m = assemble_model("HAProxy", ParsedModel(mount="//OPNsense/HAProxy/general"), {}, {}, {},
                       source="plugins", plugin=plugin)
    assert m.source == "plugins" and m.plugin == plugin
    cat = build_catalog([m], edition="community", version="26.1.9", generated_from={"plugins": "26.1.9"})
    assert cat["models"]["haproxy"]["plugin"]["package"] == "os-haproxy"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_catalog_emit.py::test_assemble_model_attaches_plugin_block -q`
Expected: FAIL — `TypeError: assemble_model() got an unexpected keyword argument 'plugin'`.

- [ ] **Step 3: Implement the passthrough**

In `backend/tools/opnsense_catalog/emit.py`, change the `assemble_model` signature and its `Model(...)` return:

```python
def assemble_model(module: str, parsed: ParsedModel, forms: dict[str, dict],
                   endpoints: dict[str, str], grid_endpoints: dict[str, dict], *, source: str,
                   plugin: dict | None = None) -> Model:
```

and the final return becomes:

```python
    return Model(id=model_root, title=module, source=source, model_root=model_root,
                 xml_path=xml_path, endpoints=endpoints,
                 fields=_label_fields(parsed.fields, forms), grids=grids, pages=page_list,
                 plugin=plugin)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_catalog_emit.py -q`
Expected: PASS (all emit tests, including the existing core ones — they omit `plugin`, defaulting to None).

- [ ] **Step 5: Commit**

```bash
git add backend/tools/opnsense_catalog/emit.py backend/tests/test_catalog_emit.py
git commit -m "feat(catalog): thread plugin metadata through assemble_model"
```

---

## Task 5: CLI — generate a plugins catalog and emit it into the shared manifest

**Files:**
- Modify: `backend/tools/opnsense_catalog/cli.py`
- Test: `backend/tests/test_catalog_cli.py` (add a case)

`generate-all` gains `--with-plugins`: for each non-skipped version it ALSO fetches the matching
`opnsense/plugins` tag, generates `community-plugins-<ver>.json` (models `source:"plugins"`, with
`plugin` blocks), and adds a `community-plugins/<ver>` manifest key — all in the one manifest pass so
the prune/incremental semantics are preserved. A version is skipped only when BOTH families are already
published. A missing plugins tag for a version degrades gracefully (warn, emit core only).

- [ ] **Step 1: Write the failing test**

First inspect the existing `test_catalog_cli.py` to reuse its source-tree fixture helper (it drives the CLI over `--source-root`/`--source`). Append a test that runs `generate-all --with-plugins` over a local source root containing a core tree and a plugins tree for one version, asserting both assets + both manifest keys exist. Add to `backend/tests/test_catalog_cli.py`:

```python
def test_generate_all_with_plugins_emits_plugins_asset_and_manifest_key(tmp_path):
    from tools.opnsense_catalog.cli import main
    # Minimal core tree for version 26.1.9
    core = tmp_path / "core" / "26.1.9" / "src/opnsense/mvc/app"
    (core / "models/OPNsense/IDS").mkdir(parents=True)
    (core / "models/OPNsense/IDS/IDS.xml").write_text(
        "<model><mount>//OPNsense/IDS</mount><items><general><enabled type='BooleanField'/></general></items></model>")
    # Minimal plugins tree for the same version
    plug = tmp_path / "plugins" / "26.1.9" / "net/haproxy"
    (plug / "src/opnsense/mvc/app/models/OPNsense/HAProxy").mkdir(parents=True)
    (plug / "Makefile").write_text("PLUGIN_NAME=\thaproxy\nPLUGIN_VERSION=\t5.1\nPLUGIN_COMMENT=\tLB\n")
    (plug / "src/opnsense/mvc/app/models/OPNsense/HAProxy/HAProxy.xml").write_text(
        "<model><mount>//OPNsense/HAProxy/general</mount><items><general><enabled type='BooleanField'/></general></items></model>")
    out = tmp_path / "out"
    rc = main(["generate-all", "--edition", "community", "--versions", "26.1.9",
               "--source-root", str(tmp_path / "core"),
               "--with-plugins", "--plugins-source-root", str(tmp_path / "plugins"),
               "--out-dir", str(out)])
    assert rc == 0
    assert (out / "community-26.1.9.json").exists()
    assert (out / "community-plugins-26.1.9.json").exists()
    manifest = json.loads((out / "manifest.json").read_text())
    assert "community/26.1.9" in manifest["catalogs"]
    assert "community-plugins/26.1.9" in manifest["catalogs"]
    import json as _j
    pcat = _j.loads((out / "community-plugins-26.1.9.json").read_text())
    assert pcat["models"]["haproxy"]["source"] == "plugins"
    assert pcat["models"]["haproxy"]["plugin"]["package"] == "os-haproxy"
```

Add `import json` at the top of the test file if not already present.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_catalog_cli.py::test_generate_all_with_plugins_emits_plugins_asset_and_manifest_key -q`
Expected: FAIL — `error: unrecognized arguments: --with-plugins`.

- [ ] **Step 3: Implement the CLI changes**

In `backend/tools/opnsense_catalog/cli.py`:

(a) Import the plugin discovery at the top, alongside the existing `discover` import:
```python
from tools.opnsense_catalog.discover import discover_models, discover_plugin_models
```

(b) Add a plugins generator next to `_generate`:
```python
def _generate_plugins(edition: str, version: str, source: Path) -> dict:
    models = []
    for pms in discover_plugin_models(source):
        src = pms.source
        parsed = parse_model(Path(src.model_xml).read_text())
        if not parsed.mount:
            print(f"SKIP {src.module}: model {src.model_xml} has no <mount>", file=sys.stderr)
            continue
        forms = parse_forms([(p.stem, p.read_text()) for p in src.form_paths])
        php = "\n".join(p.read_text() for p in src.controller_paths)
        eps, grid_eps, _conf = resolve_endpoints(src.module, parsed.grids, php or None)
        plugin = {"package": pms.plugin.package, "title": pms.plugin.title,
                  "category": pms.plugin.category, "version": pms.plugin.version}
        m = assemble_model(src.module, parsed, forms, eps, grid_eps, source="plugins", plugin=plugin)
        models.append(m)
    cat = build_catalog(models, edition=edition, version=version,
                        generated_from={"plugins": version})
    fragments = [parse_menu(p.read_text()) for p in discover_menus(source)]
    cat["menu"] = resolve_model_ids(merge_menus(fragments), set(cat["models"]))
    return cat
```

(c) Add a plugins-asset writer next to `_write_catalog`:
```python
def _write_plugins_catalog(edition: str, version: str, source: Path, out_dir: Path) -> tuple[str, bytes]:
    """Generate one plugins catalog, write <edition>-plugins-<version>.json, return (manifest-key, bytes)."""
    cat = _generate_plugins(edition, version, source)
    blob = (json.dumps(cat, indent=2, sort_keys=False) + "\n").encode("utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{edition}-plugins-{version}.json").write_bytes(blob)
    return f"{edition}-plugins/{version}", blob
```

(d) Register the new args on the `generate-all` subparser (after `ga.add_argument("--force", ...)`):
```python
    ga.add_argument("--with-plugins", action="store_true",
                    help="also generate a <edition>-plugins-<version>.json per version from opnsense/plugins")
    ga.add_argument("--plugins-source-root",
                    help="dir with one extracted plugins tree per version: <root>/<version>/ (no --fetch)")
```

(e) In the `generate-all` handler, change the per-version loop and skip logic so it emits BOTH families
and the skip requires both keys. Replace the existing loop body with:
```python
        for version in versions:
            core_key = f"{args.edition}/{version}"
            plug_key = f"{args.edition}-plugins/{version}"
            need_core = args.force or core_key not in prior
            need_plug = args.with_plugins and (args.force or plug_key not in prior)
            if not need_core and not need_plug:
                if core_key in prior:
                    carried[core_key] = prior[core_key]
                if plug_key in prior:
                    carried[plug_key] = prior[plug_key]
                skipped.append(version)
                continue
            # Core catalog
            if need_core:
                if args.fetch:
                    with tempfile.TemporaryDirectory() as tmp:
                        source = fetch_source("core", version, Path(tmp))
                        key, blob = _write_catalog(args.edition, version, source, out_dir)
                else:
                    source = Path(args.source_root) / version
                    key, blob = _write_catalog(args.edition, version, source, out_dir)
                entries[key] = blob
            elif core_key in prior:
                carried[core_key] = prior[core_key]
            # Plugins catalog
            if need_plug:
                try:
                    if args.fetch:
                        with tempfile.TemporaryDirectory() as tmp:
                            psource = fetch_source("plugins", version, Path(tmp))
                            pkey, pblob = _write_plugins_catalog(args.edition, version, psource, out_dir)
                    else:
                        psource = Path(args.plugins_source_root) / version
                        pkey, pblob = _write_plugins_catalog(args.edition, version, psource, out_dir)
                    entries[pkey] = pblob
                except Exception as exc:  # missing plugins tag for this version, etc. — degrade gracefully
                    print(f"SKIP plugins for {version}: {exc}", file=sys.stderr)
            elif plug_key in prior:
                carried[plug_key] = prior[plug_key]
```

(Leave the `manifest = build_manifest(entries)` / `manifest["catalogs"].update(carried)` /
`generated_at` / write-out lines below unchanged — they already merge `entries` + `carried`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_catalog_cli.py -q`
Expected: PASS (the new test + all existing CLI tests — core-only `generate-all` still behaves as before because `with_plugins` defaults False and the core branch is unchanged).

- [ ] **Step 5: Lint + commit**

Run: `ruff check tools/`
Expected: `All checks passed!`

```bash
git add backend/tools/opnsense_catalog/cli.py backend/tests/test_catalog_cli.py
git commit -m "feat(catalog): generate-all --with-plugins emits a per-version plugins catalog"
```

---

## Task 6: Publish workflow — emit plugin catalogs

**Files:**
- Modify: `.github/workflows/publish-catalogs.yml`

Pass `--with-plugins` to the existing generate step so each scheduled run also publishes
`community-plugins-<ver>.json` + the `community-plugins/<ver>` manifest keys (incremental, same as core).

- [ ] **Step 1: Edit the generate step**

In `.github/workflows/publish-catalogs.yml`, in the "Generate new catalogs (incremental) + Business→Community map" step, change the `generate-all` invocation to add `--with-plugins`:

```yaml
          python -m tools.opnsense_catalog.cli generate-all \
            --edition community --versions "$VERSIONS" --fetch $FORCE --with-plugins \
            --prior-manifest "$GITHUB_WORKSPACE/prior/manifest.json" \
            --out-dir "$GITHUB_WORKSPACE/out"
```

Also update the workflow's top comment to mention plugin catalogs (change "generates a versioned
API-model catalog" to "generates versioned API-model catalogs for core AND plugins").

- [ ] **Step 2: Validate the YAML parses**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/publish-catalogs.yml')); print('yaml ok')"`
Expected: `yaml ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/publish-catalogs.yml
git commit -m "ci(catalog): publish per-version plugin catalogs alongside core"
```

---

## Task 7: Consumer — `get_plugins_catalog` (fetch + SHA-verify + cache)

**Files:**
- Modify: `backend/app/services/catalog_provider.py`
- Test: `backend/tests/test_catalog_provider_plugins.py` (create)

Mirror `get_catalog` for the plugins asset family. The asset prefix `community-plugins` is a hardcoded
constant (never user/device-controlled), so only the resolved version needs gating (`_SAFE_VERSION`,
already defined). Resolve the requested version against the manifest's `community-plugins/` keys, fetch
`{base}/community-plugins-<ver>.json`, verify its SHA-256, and cache under
`CatalogCache(edition="community-plugins", version=<ver>)` (distinct from the core cache key).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_catalog_provider_plugins.py`:

```python
import hashlib
import json

import httpx
import respx
from httpx import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.catalog_cache import CatalogCache
from app.services.catalog_provider import get_plugins_catalog

_BASE = "https://catalogs.test"
_PCAT = {"edition": "community", "version": "26.1.9", "generated_from": {"plugins": "26.1.9"},
         "models": {"haproxy": {"id": "haproxy", "source": "plugins",
                                "plugin": {"package": "os-haproxy"}}}, "menu": []}
_PBLOB = json.dumps(_PCAT).encode("utf-8")
_PSHA = hashlib.sha256(_PBLOB).hexdigest()


def _mock_plugins(sha=_PSHA, blob=_PBLOB):
    respx.get(f"{_BASE}/manifest.json").mock(return_value=Response(
        200, json={"generated_at": "", "catalogs": {"community/26.1.9": "x",
                                                     "community-plugins/26.1.9": sha}}))
    respx.get(f"{_BASE}/community-plugins-26.1.9.json").mock(return_value=Response(200, content=blob))


@respx.mock
async def test_get_plugins_catalog_fetches_verifies_and_caches(db_engine):
    _mock_plugins()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_plugins_catalog(s, "community", "26.1.9", base_url=_BASE, auto_fetch=True)
        assert cat["models"]["haproxy"]["plugin"]["package"] == "os-haproxy"
        await s.commit()
    async with factory() as s:
        row = (await s.execute(
            select(CatalogCache).where(CatalogCache.edition == "community-plugins"))).scalar_one()
        assert (row.edition, row.version, row.sha256) == ("community-plugins", "26.1.9", _PSHA)


@respx.mock
async def test_get_plugins_catalog_rejects_sha_mismatch(db_engine):
    _mock_plugins(sha="deadbeef")
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_plugins_catalog(s, "community", "26.1.9", base_url=_BASE, auto_fetch=True) is None
        assert (await s.execute(select(CatalogCache))).first() is None


@respx.mock
async def test_get_plugins_catalog_floor_resolves_version(db_engine):
    # device on 26.1.10 but only 26.1.9 plugins published -> serve 26.1.9.
    _mock_plugins()
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_plugins_catalog(s, "community", "26.1.10", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.9"


@respx.mock
async def test_get_plugins_catalog_business_uses_community_plugins(db_engine):
    # Business device: resolve its base via business-base, then serve the community-plugins asset.
    respx.get(f"{_BASE}/manifest.json").mock(return_value=Response(
        200, json={"generated_at": "", "catalogs": {"community-plugins/26.1.6": _PSHA}}))
    respx.get(f"{_BASE}/business-base.json").mock(return_value=Response(200, json={"map": {"26.4": "26.1.6"}}))
    # served bytes carry version 26.1.6
    cat66 = dict(_PCAT, version="26.1.6")
    blob = json.dumps(cat66).encode()
    sha = hashlib.sha256(blob).hexdigest()
    respx.get(f"{_BASE}/manifest.json").mock(return_value=Response(
        200, json={"generated_at": "", "catalogs": {"community-plugins/26.1.6": sha}}))
    respx.get(f"{_BASE}/community-plugins-26.1.6.json").mock(return_value=Response(200, content=blob))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        cat = await get_plugins_catalog(s, "business", "26.4", base_url=_BASE, auto_fetch=True)
        assert cat["version"] == "26.1.6"


@respx.mock
async def test_get_plugins_catalog_offline_cold_returns_none(db_engine):
    respx.get(f"{_BASE}/manifest.json").mock(side_effect=httpx.ConnectError("offline"))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        assert await get_plugins_catalog(s, "community", "26.1.9", base_url=_BASE, auto_fetch=True) is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_catalog_provider_plugins.py -q`
Expected: FAIL — `ImportError: cannot import name 'get_plugins_catalog'`.

- [ ] **Step 3: Implement `get_plugins_catalog`**

In `backend/app/services/catalog_provider.py`, add a helper to list plugin versions (next to
`_community_versions`):

```python
_PLUGINS_EDITION = "community-plugins"  # the plugins asset family (constant; never user-controlled)


def _plugins_versions(manifest: dict) -> list[str]:
    prefix = f"{_PLUGINS_EDITION}/"
    return [k.split("/", 1)[1] for k in manifest.get("catalogs", {}) if k.startswith(prefix)]
```

Then append `get_plugins_catalog` (mirrors `get_catalog`; resolves the same way a device's version
floor-resolves, mapping Business → its Community base first):

```python
async def get_plugins_catalog(
    session: AsyncSession,
    edition: str,
    version: str,
    *,
    base_url: str | None = None,
    auto_fetch: bool | None = None,
) -> dict | None:
    """Resolve the device's version to the published plugins catalog, verify SHA-256 + cache, return it.

    Plugins are Community-sourced: a Business device is served the community-plugins asset of its mapped
    base version, exactly like the core catalog. Returns None when nothing resolves (offline cold, SHA
    mismatch, or no published version <= the device's)."""
    settings = get_settings()
    base = (base_url if base_url is not None else settings.catalog_release_base_url).rstrip("/")
    fetch = settings.catalog_auto_fetch if auto_fetch is None else auto_fetch
    edition = (edition or "community").lower()

    res_ver: str | None = None
    if fetch:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as http:
                manifest = (await http.get(f"{base}/manifest.json")).raise_for_status().json()
                plug_versions = _plugins_versions(manifest)
                if edition == "business":
                    business_base = (
                        await http.get(f"{base}/business-base.json")).raise_for_status().json()
                    bmap = (business_base or {}).get("map", {})
                    be = resolve_version(list(bmap), version)
                    res_ver = resolve_version(plug_versions, bmap[be]) if be is not None else None
                else:
                    res_ver = resolve_version(plug_versions, version)
                if res_ver is not None and _SAFE_VERSION.match(res_ver):
                    row = await _cache_get(session, _PLUGINS_EDITION, res_ver)
                    if row is not None:
                        return row.content
                    expected = manifest.get("catalogs", {}).get(f"{_PLUGINS_EDITION}/{res_ver}")
                    raw = (await http.get(
                        f"{base}/{_PLUGINS_EDITION}-{res_ver}.json")).raise_for_status().content
                    actual = hashlib.sha256(raw).hexdigest()
                    if not expected:
                        logger.warning("plugins catalog sha256 missing for %s — rejected", res_ver)
                    elif actual != expected:
                        logger.warning("plugins catalog sha256 mismatch for %s — rejected", res_ver)
                    else:
                        content = json.loads(raw)
                        session.add(CatalogCache(
                            edition=_PLUGINS_EDITION, version=res_ver, sha256=actual, content=content))
                        await session.flush()
                        return content
                else:
                    res_ver = None
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # fall through to the offline fallback

    if res_ver is not None:
        row = await _cache_get(session, _PLUGINS_EDITION, res_ver)
        if row is not None:
            return row.content
    return None
```

Note: the asset URL is `f"{base}/{_PLUGINS_EDITION}-{res_ver}.json"` →
`community-plugins-<ver>.json`. `_PLUGINS_EDITION` is a constant, and `res_ver` is `_SAFE_VERSION`-gated,
so no path-traversal/SSRF is possible (same guarantee `get_catalog` relies on).

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_catalog_provider_plugins.py -q`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Lint + full catalog regression + commit**

Run: `ruff check app/ tools/`
Expected: `All checks passed!`

Run: `python -m pytest tests/ -k catalog -q`
Expected: PASS (no regressions across the catalog suite).

```bash
git add backend/app/services/catalog_provider.py backend/tests/test_catalog_provider_plugins.py
git commit -m "feat(catalog): get_plugins_catalog fetches + verifies + caches the plugins asset"
```

---

## Final verification (before opening the Phase 1 PR)

- [ ] **Backend lint:** `cd backend && ruff check app/ tools/` → `All checks passed!`
- [ ] **Catalog suite:** `cd backend && python -m pytest tests/ -k catalog -q` → all pass.
- [ ] **Sanity-generate against a real plugins tag (optional, network):**
  `cd backend && python -m tools.opnsense_catalog.cli generate-all --edition community --versions 26.1.9 --fetch --with-plugins --out-dir /tmp/catout` then
  `python -c "import json;d=json.load(open('/tmp/catout/community-plugins-26.1.9.json'));print('plugin models:',len(d['models']));print('sample:',next(iter(d['models'].values()))['plugin'])"`
  Expected: a non-trivial model count and a sample `plugin` block with an `os-` package.
- [ ] Open the PR for Phase 1, get CI green, squash-merge. Then return to writing-plans for **Phase 2 (per-device install telemetry)**.

---

## Self-review notes (author)

- **Spec coverage (Phase 1 slice):** plugin catalog generation (Tasks 1–5), separate per-version asset + 1:1-core versioning (Task 5 filename/manifest keys), publish (Task 6), consumer fetch+SHA+cache (Task 7). Menu-merge-at-runtime + API exposure + telemetry + lifecycle + UI are explicitly deferred to Phases 2–4 per the scope note.
- **Never-drop:** unchanged — plugin models flow through the same `model_parser`/`assemble_model`, so unknown field classes still emit `confidence:"raw"`.
- **Security:** the plugins asset prefix is a constant; only `_SAFE_VERSION`-gated versions reach the URL/cache. No new outbound path is unguarded.
- **Type consistency:** `PluginMeta{package,title,category,version}` (Task 2) == the `plugin` dict emitted (Tasks 3–5) == what the consumer surfaces (Task 7). `assemble_model(..., plugin=)` keyword is consistent across Tasks 4–5.
