# OPNsense Schema-Catalog Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline generator that turns OPNsense's tagged open source (core, then public plugins) into a complete, versioned JSON catalog of API-modifiable models, plus a pure cross-version diff.

**Architecture:** A standalone, app-independent package under `backend/tools/opnsense_catalog/` of small pure units (parse model XML → fields/grids, parse forms → labels, resolve API endpoints, discover models in a source tree, emit a stable-ordered catalog JSON + coverage report, diff two catalogs) behind a thin network Fetcher and a CLI. The **never-drop** principle guarantees total coverage: an unrecognised field class or unresolved endpoint is emitted as `confidence:"raw"`, never omitted.

**Tech Stack:** Python 3.14, stdlib `xml.etree`/`defusedxml`, `dataclasses`, `argparse`, `httpx` (fetch), `pytest`. No new dependencies. venv at `backend/.venv`; run tests from `backend/`.

---

## Conventions for the implementer

- Work in `backend/`. Activate nothing — call `.venv/bin/python -m pytest …` directly.
- DB-free: this whole sub-project is pure + file I/O; **no `TEST_DATABASE_URL` needed** for these tests.
- `ruff check` must stay clean (`backend/.venv/bin/ruff check <files>`).
- English everywhere. Commit from the repo root (`cd /home/l0rdg3x/coding/OPNGMS`).
- All catalog field `type` values are from this fixed set: `bool`, `int`, `string`, `enum`, `multienum`, `network`, `ref`. Unknown OPNsense field classes map to `string` with `confidence:"raw"`.

## File structure (created by this plan)

| File | Responsibility |
|------|----------------|
| `backend/tools/__init__.py` | namespace package marker (empty) |
| `backend/tools/opnsense_catalog/__init__.py` | package marker (empty) |
| `backend/tools/opnsense_catalog/types.py` | dataclasses `Field`, `Grid`, `Model`, `ParsedModel` + `to_dict` helpers |
| `backend/tools/opnsense_catalog/model_parser.py` | `parse_model(xml) -> ParsedModel` (field-class map, Multiple, ArrayField→grid, never-drop) |
| `backend/tools/opnsense_catalog/form_parser.py` | `parse_forms(xmls) -> {field_id: {label,help,page}}` |
| `backend/tools/opnsense_catalog/endpoints.py` | `resolve_endpoints(module, parsed, controller_php) -> (endpoints, grid_endpoints, confidence)` |
| `backend/tools/opnsense_catalog/discover.py` | `discover_models(root) -> list[ModelSource]` (walk an extracted source tree) |
| `backend/tools/opnsense_catalog/emit.py` | `build_catalog(...) -> dict` (stable order) + `coverage_report(catalog) -> dict` |
| `backend/tools/opnsense_catalog/diff.py` | `diff_catalogs(a, b) -> dict` (pure) |
| `backend/tools/opnsense_catalog/fetch.py` | `fetch_source(repo, ref, dest) -> Path` (codeload tarball; the only network unit) |
| `backend/tools/opnsense_catalog/cli.py` | `main(argv)`: `generate` + `diff` subcommands |
| `backend/tests/fixtures/opnsense_catalog/…` | vendored real model/form/controller snippets + golden JSON |
| `backend/tests/test_catalog_*.py` | one test module per unit |

---

### Task 1: Catalog types

**Files:**
- Create: `backend/tools/__init__.py` (empty), `backend/tools/opnsense_catalog/__init__.py` (empty)
- Create: `backend/tools/opnsense_catalog/types.py`
- Test: `backend/tests/test_catalog_types.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_types.py
from tools.opnsense_catalog.types import Field, Grid, Model, model_to_dict


def test_field_defaults_and_dict():
    f = Field(path="general.enabled", type="bool")
    assert f.required is False and f.confidence == "rich" and f.options == []


def test_model_to_dict_is_stable_and_sorted():
    m = Model(
        id="ids.general", title="IDS", source="core", model_root="ids", xml_path="OPNsense/IDS",
        endpoints={"set": "ids/settings/set", "get": "ids/settings/get"},
        fields=[Field(path="general.ips", type="bool"), Field(path="general.enabled", type="bool")],
        grids=[Grid(path="userrules", endpoints={"add": "ids/settings/addUserrule"},
                    fields=[Field(path="enabled", type="bool")])],
        pages=[{"id": "general", "label": "General", "fields": ["general.enabled"]}],
    )
    d = model_to_dict(m)
    # endpoints + fields are sorted for clean file diffs
    assert list(d["endpoints"]) == ["get", "set"]
    assert [f["path"] for f in d["fields"]] == ["general.enabled", "general.ips"]
    assert d["grids"][0]["path"] == "userrules"
    # rich-only keys omitted when default to keep JSON tight
    assert "options" not in d["fields"][0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_types.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.opnsense_catalog'`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/types.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Field:
    path: str
    type: str                          # bool|int|string|enum|multienum|network|ref
    required: bool = False
    default: str | None = None
    options: list[str] = field(default_factory=list)
    label: str = ""
    help: str = ""
    confidence: str = "rich"           # rich|raw


@dataclass
class Grid:
    path: str
    endpoints: dict[str, str] = field(default_factory=dict)
    fields: list[Field] = field(default_factory=list)


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


@dataclass
class ParsedModel:
    """What model_parser produces before forms/endpoints are merged in."""
    mount: str                          # e.g. //OPNsense/IDS
    fields: list[Field] = field(default_factory=list)
    grids: list[Grid] = field(default_factory=list)


def _field_to_dict(f: Field) -> dict:
    out: dict = {"path": f.path, "type": f.type, "confidence": f.confidence}
    if f.required:
        out["required"] = True
    if f.default is not None:
        out["default"] = f.default
    if f.options:
        out["options"] = list(f.options)
    if f.label:
        out["label"] = f.label
    if f.help:
        out["help"] = f.help
    return out


def _grid_to_dict(g: Grid) -> dict:
    return {"path": g.path, "endpoints": dict(sorted(g.endpoints.items())),
            "fields": [_field_to_dict(x) for x in sorted(g.fields, key=lambda f: f.path)]}


def model_to_dict(m: Model) -> dict:
    return {
        "id": m.id, "title": m.title, "source": m.source, "model_root": m.model_root,
        "xml_path": m.xml_path,
        "endpoints": dict(sorted(m.endpoints.items())),
        "pages": m.pages,
        "fields": [_field_to_dict(f) for f in sorted(m.fields, key=lambda f: f.path)],
        "grids": [_grid_to_dict(g) for g in sorted(m.grids, key=lambda g: g.path)],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_types.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/__init__.py backend/tools/opnsense_catalog/__init__.py backend/tools/opnsense_catalog/types.py backend/tests/test_catalog_types.py
git commit -m "feat(catalog): catalog dataclasses + stable to_dict"
```

---

### Task 2: Model parser — scalar fields + field-class map + never-drop

**Files:**
- Create: `backend/tools/opnsense_catalog/model_parser.py`
- Test: `backend/tests/test_catalog_model_parser.py`

**Background — OPNsense model XML shape:** a `<model>` has a `<mount>` (e.g. `//OPNsense/IDS`) and an `<items>` tree. Each leaf field is an element whose **`type` attribute** is the field class (`BooleanField`, `IntegerField`, `TextField`, `OptionField`, `NetworkField`, `ModelRelationField`, …). Children `<Required>Y</Required>`, `<Multiple>Y</Multiple>`, `<default>…</default>`, and for `OptionField` a `<OptionValues>` with child option elements. The field **path** is the dotted chain of element tags under `<items>` (e.g. `general.enabled`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_model_parser.py
from tools.opnsense_catalog.model_parser import parse_model

_XML = """
<model>
  <mount>//OPNsense/IDS</mount>
  <items>
    <general>
      <enabled type="BooleanField"><default>0</default><Required>N</Required></enabled>
      <detail type="TextField"/>
      <maxpkt type="IntegerField"><default>1500</default></maxpkt>
      <homenet type="NetworkField"><Multiple>Y</Multiple></homenet>
      <ruleset type="OptionField">
        <OptionValues><et>ET open</et><abuse>Abuse.ch</abuse></OptionValues>
      </ruleset>
      <categories type="OptionField">
        <Multiple>Y</Multiple><OptionValues><a>A</a><b>B</b></OptionValues>
      </categories>
      <weirdo type="SomeFutureField"/>
    </general>
  </items>
</model>
"""


def test_parses_scalar_field_classes_with_paths_and_types():
    pm = parse_model(_XML)
    assert pm.mount == "//OPNsense/IDS"
    by_path = {f.path: f for f in pm.fields}
    assert by_path["general.enabled"].type == "bool"
    assert by_path["general.enabled"].default == "0"
    assert by_path["general.detail"].type == "string"
    assert by_path["general.maxpkt"].type == "int" and by_path["general.maxpkt"].default == "1500"
    assert by_path["general.homenet"].type == "network"            # NetworkField stays network (Multiple is a UI hint)
    assert by_path["general.ruleset"].type == "enum"
    assert by_path["general.ruleset"].options == ["ET open", "Abuse.ch"]
    assert by_path["general.categories"].type == "multienum"       # OptionField + Multiple -> multienum
    assert by_path["general.categories"].options == ["A", "B"]


def test_unknown_field_class_is_raw_never_dropped():
    pm = parse_model(_XML)
    weird = next(f for f in pm.fields if f.path == "general.weirdo")
    assert weird.type == "string" and weird.confidence == "raw"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_model_parser.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/model_parser.py
from __future__ import annotations

from defusedxml import ElementTree as DET

from tools.opnsense_catalog.types import Field, ParsedModel

# OPNsense field class -> our catalog type. Unknown -> raw string (never-drop).
_TYPE_MAP = {
    "BooleanField": "bool",
    "IntegerField": "int",
    "PortField": "int",
    "TextField": "string",
    "DescriptionField": "string",
    "HostnameField": "string",
    "EmailField": "string",
    "NetworkField": "network",
    "NetworkAliasField": "network",
    "OptionField": "enum",
    "ModelRelationField": "ref",
    "NetworkField_IPv4": "network",
}


def _text(el, tag: str) -> str | None:
    child = el.find(tag)
    return child.text if child is not None and child.text is not None else None


def _is_truthy(el, tag: str) -> bool:
    return (_text(el, tag) or "").strip().upper() in ("Y", "YES", "1", "TRUE")


def _options(el) -> list[str]:
    ov = el.find("OptionValues")
    if ov is None:
        return []
    return [(opt.text or opt.tag) for opt in list(ov)]


def _walk(node, prefix: str, fields: list[Field]) -> None:
    for child in list(node):
        tag = child.tag
        path = f"{prefix}.{tag}" if prefix else tag
        cls = child.get("type")
        if cls is None:
            # a container node (no type attr) -> recurse
            _walk(child, path, fields)
            continue
        base = _TYPE_MAP.get(cls)
        confidence = "rich" if base is not None else "raw"
        ftype = base or "string"
        if base == "enum" and _is_truthy(child, "Multiple"):
            ftype = "multienum"
        fields.append(Field(
            path=path, type=ftype, required=_is_truthy(child, "Required"),
            default=_text(child, "default"), options=_options(child), confidence=confidence,
        ))


def parse_model(xml_text: str) -> ParsedModel:
    root = DET.fromstring(xml_text)
    mount = (root.findtext("mount") or "").strip()
    items = root.find("items")
    fields: list[Field] = []
    if items is not None:
        _walk(items, "", fields)
    return ParsedModel(mount=mount, fields=fields, grids=[])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_model_parser.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/model_parser.py backend/tests/test_catalog_model_parser.py
git commit -m "feat(catalog): model parser — scalar field-class map + never-drop raw"
```

---

### Task 3: Model parser — ArrayField → grid

**Files:**
- Modify: `backend/tools/opnsense_catalog/model_parser.py`
- Test: `backend/tests/test_catalog_model_parser_grid.py`

**Background:** A node with `type="ArrayField"` is a **grid** (a list of repeatable items); its child elements are the item's fields. Grids are edited through `search/add/set/del` endpoints, not the scalar `set` — so they must be captured separately, not flattened into `fields`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_model_parser_grid.py
from tools.opnsense_catalog.model_parser import parse_model

_XML = """
<model>
  <mount>//OPNsense/IDS</mount>
  <items>
    <general><enabled type="BooleanField"/></general>
    <userDefinedRules>
      <rule type="ArrayField">
        <enabled type="BooleanField"><default>1</default></enabled>
        <description type="TextField"/>
      </rule>
    </userDefinedRules>
  </items>
</model>
"""


def test_arrayfield_becomes_a_grid_with_item_fields():
    pm = parse_model(_XML)
    # the scalar walk still finds general.enabled, but NOT the grid's inner fields
    assert {f.path for f in pm.fields} == {"general.enabled"}
    assert len(pm.grids) == 1
    g = pm.grids[0]
    assert g.path == "userDefinedRules.rule"
    assert {f.path for f in g.fields} == {"enabled", "description"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_model_parser_grid.py -q`
Expected: FAIL — grid not parsed (`len(pm.grids) == 0`).

- [ ] **Step 3: Write minimal implementation** — replace `_walk` and `parse_model` in `model_parser.py`:

```python
def _walk(node, prefix: str, fields: list[Field], grids) -> None:
    from tools.opnsense_catalog.types import Grid
    for child in list(node):
        tag = child.tag
        path = f"{prefix}.{tag}" if prefix else tag
        cls = child.get("type")
        if cls == "ArrayField":
            item_fields: list[Field] = []
            _walk(child, "", item_fields, [])           # item fields are relative to the row
            grids.append(Grid(path=path, fields=item_fields))
            continue
        if cls is None:
            _walk(child, path, fields, grids)
            continue
        base = _TYPE_MAP.get(cls)
        confidence = "rich" if base is not None else "raw"
        ftype = base or "string"
        if base == "enum" and _is_truthy(child, "Multiple"):
            ftype = "multienum"
        fields.append(Field(
            path=path, type=ftype, required=_is_truthy(child, "Required"),
            default=_text(child, "default"), options=_options(child), confidence=confidence,
        ))


def parse_model(xml_text: str) -> ParsedModel:
    root = DET.fromstring(xml_text)
    mount = (root.findtext("mount") or "").strip()
    items = root.find("items")
    fields: list[Field] = []
    grids: list = []
    if items is not None:
        _walk(items, "", fields, grids)
    return ParsedModel(mount=mount, fields=fields, grids=grids)
```

- [ ] **Step 4: Run tests (both parser modules) to verify they pass**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_model_parser.py tests/test_catalog_model_parser_grid.py -q`
Expected: PASS (3 passed — the Task-2 scalar test is unaffected; the grid's inner fields are no longer flattened into `pm.fields`).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/model_parser.py backend/tests/test_catalog_model_parser_grid.py
git commit -m "feat(catalog): model parser — ArrayField -> grid"
```

---

### Task 4: Form parser — labels / help / page

**Files:**
- Create: `backend/tools/opnsense_catalog/form_parser.py`
- Test: `backend/tests/test_catalog_form_parser.py`

**Background:** OPNsense form XML (`…/forms/<name>.xml`) is a `<form>` of `<field>` entries: `<id>general.enabled</id>`, `<label>Enabled</label>`, `<help>…</help>`, grouped under `<tab>`/section via a `<tab id=… description=…>` wrapper or a `type=header` field. We extract `{id: {label, help, page}}`. Use the form file name (sans extension) as the `page` when no explicit tab id is present.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_form_parser.py
from tools.opnsense_catalog.form_parser import parse_forms

_FORM = """
<form>
  <field><id>general.enabled</id><label>Enabled</label><help>Turn IDS on</help></field>
  <field><type>header</type><label>Advanced</label></field>
  <field><id>general.detail</id><label>Detail level</label></field>
</form>
"""


def test_extracts_label_help_keyed_by_field_id():
    out = parse_forms([("general", _FORM)])
    assert out["general.enabled"]["label"] == "Enabled"
    assert out["general.enabled"]["help"] == "Turn IDS on"
    assert out["general.enabled"]["page"] == "general"
    assert out["general.detail"]["label"] == "Detail level"
    # header (no id) is ignored, not crashed on
    assert "" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_form_parser.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/form_parser.py
from __future__ import annotations

from defusedxml import ElementTree as DET


def parse_forms(named_xmls: list[tuple[str, str]]) -> dict[str, dict]:
    """named_xmls: [(form_name, xml_text)]. Returns {field_id: {label, help, page}}."""
    out: dict[str, dict] = {}
    for page, xml_text in named_xmls:
        try:
            root = DET.fromstring(xml_text)
        except Exception:  # noqa: BLE001 - a malformed form must not abort the whole model
            continue
        for fld in root.iter("field"):
            fid = (fld.findtext("id") or "").strip()
            if not fid:
                continue
            out[fid] = {
                "label": (fld.findtext("label") or "").strip(),
                "help": (fld.findtext("help") or "").strip(),
                "page": page,
            }
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_form_parser.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/form_parser.py backend/tests/test_catalog_form_parser.py
git commit -m "feat(catalog): form parser — labels/help/page by field id"
```

---

### Task 5: Endpoint resolver — convention + PHP confirm

**Files:**
- Create: `backend/tools/opnsense_catalog/endpoints.py`
- Test: `backend/tests/test_catalog_endpoints.py`

**Background:** Most modules follow `<module>/settings/get|set` + `<module>/service/reconfigure`; grids follow `<module>/settings/search<Item>/add<Item>/set<Item>/del<Item>` where `<Item>` is the grid's row tag capitalised. The API controller PHP confirms the module is MVC-standard (it extends `ApiMutableModelControllerBase`); if it doesn't, we flag the model `confidence:"raw"` so the UI treats its endpoints as unverified.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_endpoints.py
from tools.opnsense_catalog.endpoints import resolve_endpoints
from tools.opnsense_catalog.types import Grid

_STD_PHP = "class GeneralController extends ApiMutableModelControllerBase { ... }"


def test_convention_endpoints_for_settings_and_grids():
    grids = [Grid(path="userDefinedRules.rule", fields=[])]
    eps, grid_eps, confidence = resolve_endpoints("IDS", grids, _STD_PHP)
    assert eps == {"get": "ids/settings/get", "set": "ids/settings/set",
                   "reconfigure": "ids/service/reconfigure"}
    assert grid_eps["userDefinedRules.rule"] == {
        "search": "ids/settings/searchRule", "add": "ids/settings/addRule",
        "set": "ids/settings/setRule", "del": "ids/settings/delRule"}
    assert confidence == "rich"


def test_non_mvc_controller_marks_raw():
    _, _, confidence = resolve_endpoints("Weird", [], "class X extends ApiControllerBase {}")
    assert confidence == "raw"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_endpoints.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/endpoints.py
from __future__ import annotations

from tools.opnsense_catalog.types import Grid


def resolve_endpoints(module: str, grids: list[Grid], controller_php: str | None
                      ) -> tuple[dict[str, str], dict[str, dict], str]:
    base = module.lower()
    endpoints = {
        "get": f"{base}/settings/get",
        "set": f"{base}/settings/set",
        "reconfigure": f"{base}/service/reconfigure",
    }
    grid_eps: dict[str, dict] = {}
    for g in grids:
        item = g.path.split(".")[-1]
        cap = item[:1].upper() + item[1:]
        grid_eps[g.path] = {
            "search": f"{base}/settings/search{cap}",
            "add": f"{base}/settings/add{cap}",
            "set": f"{base}/settings/set{cap}",
            "del": f"{base}/settings/del{cap}",
        }
    # MVC-standard controllers extend ApiMutableModelControllerBase; otherwise endpoints are unverified.
    confidence = "rich" if (controller_php and "ApiMutableModelControllerBase" in controller_php) else "raw"
    return endpoints, grid_eps, confidence
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_endpoints.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/endpoints.py backend/tests/test_catalog_endpoints.py
git commit -m "feat(catalog): endpoint resolver — convention + MVC controller confirm"
```

---

### Task 6: Model discovery — walk an extracted source tree

**Files:**
- Create: `backend/tools/opnsense_catalog/discover.py`
- Test: `backend/tests/test_catalog_discover.py`

**Background:** In an extracted core checkout, models live at `**/mvc/app/models/OPNsense/<Module>/<Model>.xml`, forms at `**/mvc/app/views/OPNsense/<Module>/forms/*.xml`, controllers at `**/mvc/app/controllers/OPNsense/<Module>/Api/*.php`. `discover_models` pairs them by `<Module>`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_discover.py
from pathlib import Path

from tools.opnsense_catalog.discover import discover_models


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_pairs_model_with_module_forms_and_controllers(tmp_path):
    base = tmp_path / "core-26.1.8/src/opnsense/mvc/app"
    _write(base / "models/OPNsense/IDS/IDS.xml", "<model><mount>//OPNsense/IDS</mount></model>")
    _write(base / "views/OPNsense/IDS/forms/general.xml", "<form/>")
    _write(base / "controllers/OPNsense/IDS/Api/GeneralController.php", "class G {}")
    sources = discover_models(tmp_path)
    assert len(sources) == 1
    s = sources[0]
    assert s.module == "IDS"
    assert s.model_xml.endswith("IDS/IDS.xml")
    assert [p.name for p in s.form_paths] == ["general.xml"]
    assert [p.name for p in s.controller_paths] == ["GeneralController.php"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_discover.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/discover.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ModelSource:
    module: str
    model_xml: str
    form_paths: list[Path] = field(default_factory=list)
    controller_paths: list[Path] = field(default_factory=list)


def discover_models(root: Path) -> list[ModelSource]:
    out: list[ModelSource] = []
    for model_xml in sorted(root.rglob("mvc/app/models/OPNsense/*/*.xml")):
        module = model_xml.parent.name
        app = model_xml.parents[3]                      # .../mvc/app
        forms = sorted((app / "views/OPNsense" / module / "forms").glob("*.xml")) \
            if (app / "views/OPNsense" / module / "forms").is_dir() else []
        ctrls = sorted((app / "controllers/OPNsense" / module / "Api").glob("*.php")) \
            if (app / "controllers/OPNsense" / module / "Api").is_dir() else []
        out.append(ModelSource(module=module, model_xml=str(model_xml),
                               form_paths=forms, controller_paths=ctrls))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_discover.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/discover.py backend/tests/test_catalog_discover.py
git commit -m "feat(catalog): discover models/forms/controllers in a source tree"
```

---

### Task 7: Emitter + coverage report

**Files:**
- Create: `backend/tools/opnsense_catalog/emit.py`
- Test: `backend/tests/test_catalog_emit.py`

**Responsibility:** merge a `ParsedModel` + form labels + endpoints into a `Model`, assemble many into a catalog dict (stable order), and compute a coverage report (rich vs raw counts).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_emit.py
from tools.opnsense_catalog.emit import assemble_model, build_catalog, coverage_report
from tools.opnsense_catalog.types import Field, Grid, ParsedModel


def _parsed():
    return ParsedModel(mount="//OPNsense/IDS",
                       fields=[Field(path="general.enabled", type="bool"),
                               Field(path="general.x", type="string", confidence="raw")],
                       grids=[Grid(path="rules.rule", fields=[Field(path="enabled", type="bool")])])


def test_assemble_merges_labels_endpoints_and_derives_ids():
    forms = {"general.enabled": {"label": "Enabled", "help": "h", "page": "general"}}
    eps = {"get": "ids/settings/get"}
    grid_eps = {"rules.rule": {"add": "ids/settings/addRule"}}
    m = assemble_model("IDS", _parsed(), forms, eps, grid_eps, source="core")
    assert m.id == "ids" and m.model_root == "ids" and m.xml_path == "OPNsense/IDS"
    assert next(f for f in m.fields if f.path == "general.enabled").label == "Enabled"
    assert m.grids[0].endpoints == {"add": "ids/settings/addRule"}


def test_build_catalog_and_coverage():
    m = assemble_model("IDS", _parsed(), {}, {"get": "ids/settings/get"}, {}, source="core")
    cat = build_catalog([m], edition="community", version="26.1.8",
                        generated_from={"core": "26.1.8"})
    assert cat["edition"] == "community" and cat["version"] == "26.1.8"
    assert "ids" in cat["models"]
    rep = coverage_report(cat)
    assert rep["models"] == 1 and rep["fields_total"] == 2 and rep["fields_raw"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_emit.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/emit.py
from __future__ import annotations

from tools.opnsense_catalog.types import Field, Model, ParsedModel, model_to_dict


def _label_fields(fields: list[Field], forms: dict[str, dict]) -> list[Field]:
    out: list[Field] = []
    for f in fields:
        meta = forms.get(f.path, {})
        out.append(Field(path=f.path, type=f.type, required=f.required, default=f.default,
                         options=f.options, confidence=f.confidence,
                         label=meta.get("label", "") or f.label,
                         help=meta.get("help", "") or f.help))
    return out


def assemble_model(module: str, parsed: ParsedModel, forms: dict[str, dict],
                   endpoints: dict[str, str], grid_endpoints: dict[str, dict], *, source: str) -> Model:
    model_root = parsed.mount.rstrip("/").split("/")[-1].lower()
    xml_path = parsed.mount.strip("/")
    grids = []
    for g in parsed.grids:
        g.endpoints = grid_endpoints.get(g.path, {})
        g.fields = _label_fields(g.fields, forms)
        grids.append(g)
    pages: dict[str, list[str]] = {}
    for f in parsed.fields:
        pages.setdefault(forms.get(f.path, {}).get("page", ""), []).append(f.path)
    page_list = [{"id": p or "general", "fields": sorted(fs)} for p, fs in sorted(pages.items())]
    return Model(id=model_root, title=module, source=source, model_root=model_root,
                 xml_path=xml_path, endpoints=endpoints,
                 fields=_label_fields(parsed.fields, forms), grids=grids, pages=page_list)


def build_catalog(models: list[Model], *, edition: str, version: str, generated_from: dict) -> dict:
    return {
        "edition": edition, "version": version, "generated_from": generated_from,
        "models": {m.id: model_to_dict(m) for m in sorted(models, key=lambda m: m.id)},
    }


def coverage_report(catalog: dict) -> dict:
    total = raw = 0
    for m in catalog["models"].values():
        for f in m["fields"]:
            total += 1
            raw += 1 if f.get("confidence") == "raw" else 0
    return {"models": len(catalog["models"]), "fields_total": total, "fields_raw": raw}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_emit.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/emit.py backend/tests/test_catalog_emit.py
git commit -m "feat(catalog): emitter — assemble model + build catalog + coverage report"
```

---

### Task 8: Differ

**Files:**
- Create: `backend/tools/opnsense_catalog/diff.py`
- Test: `backend/tests/test_catalog_diff.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_diff.py
from tools.opnsense_catalog.diff import diff_catalogs


def _cat(models):
    return {"edition": "community", "version": "x", "models": models}


def _model(fields):
    return {"fields": [{"path": p, "type": t} for p, t in fields]}


def test_diff_reports_added_removed_and_changed():
    a = _cat({"ids": _model([("general.enabled", "bool"), ("general.old", "string")]),
              "gone": _model([])})
    b = _cat({"ids": _model([("general.enabled", "int"), ("general.new", "bool")]),
              "added": _model([])})
    d = diff_catalogs(a, b)
    assert d["added_models"] == ["added"]
    assert d["removed_models"] == ["gone"]
    ids = d["models"]["ids"]
    assert ids["added_fields"] == ["general.new"]
    assert ids["removed_fields"] == ["general.old"]
    assert ids["changed_fields"] == [{"path": "general.enabled", "attr": "type",
                                       "before": "bool", "after": "int"}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_diff.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/diff.py
from __future__ import annotations


def _fields_by_path(model: dict) -> dict[str, dict]:
    return {f["path"]: f for f in model.get("fields", [])}


def diff_catalogs(a: dict, b: dict) -> dict:
    am, bm = a.get("models", {}), b.get("models", {})
    added_models = sorted(set(bm) - set(am))
    removed_models = sorted(set(am) - set(bm))
    models: dict[str, dict] = {}
    for mid in sorted(set(am) & set(bm)):
        af, bf = _fields_by_path(am[mid]), _fields_by_path(bm[mid])
        added = sorted(set(bf) - set(af))
        removed = sorted(set(af) - set(bf))
        changed = []
        for path in sorted(set(af) & set(bf)):
            for attr in ("type", "required", "default", "options"):
                if af[path].get(attr) != bf[path].get(attr):
                    changed.append({"path": path, "attr": attr,
                                    "before": af[path].get(attr), "after": bf[path].get(attr)})
        if added or removed or changed:
            models[mid] = {"added_fields": added, "removed_fields": removed, "changed_fields": changed}
    return {"added_models": added_models, "removed_models": removed_models, "models": models}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_diff.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/diff.py backend/tests/test_catalog_diff.py
git commit -m "feat(catalog): pure cross-version catalog differ"
```

---

### Task 9: Fetcher (codeload tarball)

**Files:**
- Create: `backend/tools/opnsense_catalog/fetch.py`
- Test: `backend/tests/test_catalog_fetch.py`

**Responsibility:** download a tag tarball and extract it. The test does NOT hit the network: it builds a tiny `.tar.gz` locally and verifies extraction. The live download is a thin `httpx.get` wrapper exercised only by the CLI integration run.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_fetch.py
import io
import tarfile

from tools.opnsense_catalog.fetch import extract_tarball


def test_extract_tarball_unpacks_to_dest(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"<model/>"
        info = tarfile.TarInfo("core-26.1.8/src/x/IDS.xml")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    dest = tmp_path / "out"
    root = extract_tarball(buf.getvalue(), dest)
    assert (root / "core-26.1.8/src/x/IDS.xml").read_bytes() == b"<model/>"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_fetch.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/fetch.py
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import httpx

_CODELOAD = "https://codeload.github.com/opnsense/{repo}/tar.gz/refs/tags/{ref}"


def extract_tarball(data: bytes, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        # Path-traversal guard: refuse any member that escapes dest.
        for member in tf.getmembers():
            target = (dest / member.name).resolve()
            if not str(target).startswith(str(dest.resolve())):
                raise ValueError(f"unsafe tar member: {member.name}")
        tf.extractall(dest)  # noqa: S202 - members validated above
    return dest


def fetch_source(repo: str, ref: str, dest: Path, *, timeout: float = 60.0) -> Path:
    """Download opnsense/<repo> at tag <ref> and extract to dest. Network: CLI use only."""
    url = _CODELOAD.format(repo=repo, ref=ref)
    resp = httpx.get(url, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return extract_tarball(resp.content, dest)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_fetch.py -q`
Expected: PASS (1 passed).

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/fetch.py backend/tests/test_catalog_fetch.py
git commit -m "feat(catalog): tarball fetcher + path-traversal-safe extract"
```

---

### Task 10: CLI — `generate` and `diff`, with a golden end-to-end test

**Files:**
- Create: `backend/tools/opnsense_catalog/cli.py`
- Create: `backend/tests/fixtures/opnsense_catalog/minicore/src/opnsense/mvc/app/models/OPNsense/IDS/IDS.xml`
- Create: `backend/tests/fixtures/opnsense_catalog/minicore/src/opnsense/mvc/app/views/OPNsense/IDS/forms/general.xml`
- Create: `backend/tests/fixtures/opnsense_catalog/minicore/src/opnsense/mvc/app/controllers/OPNsense/IDS/Api/GeneralController.php`
- Test: `backend/tests/test_catalog_cli.py`

**Responsibility:** orchestrate discover → parse → forms → endpoints → emit over an already-extracted tree (the CLI's `--source` path), so the end-to-end test needs no network. A separate `--fetch` mode calls `fetch_source` first.

- [ ] **Step 1: Create the fixture tree** (vendored mini source; replace later with the real pinned files)

`…/models/OPNsense/IDS/IDS.xml`:
```xml
<model>
  <mount>//OPNsense/IDS</mount>
  <items>
    <general>
      <enabled type="BooleanField"><default>0</default></enabled>
      <homenet type="NetworkField"><Multiple>Y</Multiple></homenet>
    </general>
  </items>
</model>
```
`…/views/OPNsense/IDS/forms/general.xml`:
```xml
<form><field><id>general.enabled</id><label>Enabled</label><help>Turn IDS on</help></field></form>
```
`…/controllers/OPNsense/IDS/Api/GeneralController.php`:
```php
<?php class GeneralController extends ApiMutableModelControllerBase {}
```

- [ ] **Step 2: Write the failing test**

```python
# backend/tests/test_catalog_cli.py
import json
from pathlib import Path

from tools.opnsense_catalog.cli import main

_FIX = Path(__file__).parent / "fixtures/opnsense_catalog/minicore"


def test_generate_writes_catalog(tmp_path):
    out = tmp_path / "26.1.8.json"
    rc = main(["generate", "--edition", "community", "--version", "26.1.8",
               "--source", str(_FIX), "--out", str(out)])
    assert rc == 0
    cat = json.loads(out.read_text())
    assert cat["version"] == "26.1.8"
    ids = cat["models"]["ids"]
    assert ids["endpoints"]["set"] == "ids/settings/set"
    enabled = next(f for f in ids["fields"] if f["path"] == "general.enabled")
    assert enabled["type"] == "bool" and enabled["label"] == "Enabled"


def test_diff_command(tmp_path, capsys):
    a = tmp_path / "a.json"; b = tmp_path / "b.json"
    a.write_text(json.dumps({"models": {"ids": {"fields": [{"path": "p", "type": "bool"}]}}}))
    b.write_text(json.dumps({"models": {"ids": {"fields": [{"path": "p", "type": "int"}]}}}))
    rc = main(["diff", str(a), str(b)])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["models"]["ids"]["changed_fields"][0]["after"] == "int"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_cli.py -q`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 4: Write minimal implementation**

```python
# backend/tools/opnsense_catalog/cli.py
from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from tools.opnsense_catalog.diff import diff_catalogs
from tools.opnsense_catalog.discover import discover_models
from tools.opnsense_catalog.emit import assemble_model, build_catalog, coverage_report
from tools.opnsense_catalog.endpoints import resolve_endpoints
from tools.opnsense_catalog.fetch import fetch_source
from tools.opnsense_catalog.form_parser import parse_forms
from tools.opnsense_catalog.model_parser import parse_model


def _generate(edition: str, version: str, source: Path) -> dict:
    models = []
    for src in discover_models(source):
        parsed = parse_model(Path(src.model_xml).read_text())
        if not parsed.mount:
            continue
        forms = parse_forms([(p.stem, p.read_text()) for p in src.form_paths])
        php = "\n".join(p.read_text() for p in src.controller_paths)
        eps, grid_eps, _conf = resolve_endpoints(src.module, parsed.grids, php or None)
        m = assemble_model(src.module, parsed, forms, eps, grid_eps, source="core")
        models.append(m)
    return build_catalog(models, edition=edition, version=version,
                         generated_from={"core": version})


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="opnsense-catalog")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--edition", default="community")
    g.add_argument("--version", required=True)
    g.add_argument("--source", help="path to an extracted source tree")
    g.add_argument("--fetch", action="store_true", help="download the tag first")
    g.add_argument("--out", required=True)
    d = sub.add_parser("diff")
    d.add_argument("a"); d.add_argument("b")
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        if args.fetch:
            tmp = Path(tempfile.mkdtemp())
            source = fetch_source("core", args.version, tmp)
        else:
            source = Path(args.source)
        cat = _generate(args.edition, args.version, source)
        Path(args.out).write_text(json.dumps(cat, indent=2, sort_keys=False) + "\n")
        rep = coverage_report(cat)
        print(json.dumps({"wrote": args.out, "coverage": rep}))
        return 0
    if args.cmd == "diff":
        a = json.loads(Path(args.a).read_text())
        b = json.loads(Path(args.b).read_text())
        print(json.dumps(diff_catalogs(a, b), indent=2))
        return 0
    return 1
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_cli.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Run the whole catalog suite + ruff**

Run:
```bash
cd backend && .venv/bin/ruff check tools/opnsense_catalog tests/test_catalog_*.py && \
  .venv/bin/python -m pytest tests/test_catalog_*.py -q
```
Expected: ruff clean; all catalog tests pass.

- [ ] **Step 7: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tools/opnsense_catalog/cli.py backend/tests/test_catalog_cli.py backend/tests/fixtures/opnsense_catalog/minicore
git commit -m "feat(catalog): CLI generate+diff with a no-network golden e2e test"
```

---

### Task 11: Prove on real OPNsense — vendor 3 real models + run core, gate coverage

**Files:**
- Create: `backend/tests/fixtures/opnsense_catalog/real/` (real pinned `IDS.xml`, `unbound.xml`/`Unbound.xml`, `Monit.xml` + their forms/controllers from the tags)
- Test: `backend/tests/test_catalog_real_pilot.py`
- Create: `backend/tools/opnsense_catalog/README.md` (how to regenerate a full catalog)

**Background:** Now validate the generic engine against **real** OPNsense definitions. Vendor the actual files from the pinned tags (paths below), and assert the engine produces sane, rich (non-raw-majority) output across 3 modules — proving generality before a full-core run.

Real source paths to vendor (from `github.com/opnsense/core` at tag `26.1.8`):
- `src/opnsense/mvc/app/models/OPNsense/IDS/IDS.xml` (+ `views/OPNsense/IDS/forms/*.xml`, `controllers/OPNsense/IDS/Api/*.php`)
- `src/opnsense/mvc/app/models/OPNsense/Unbound/Unbound.xml` (+ forms + Api)
- `src/opnsense/mvc/app/models/OPNsense/Monit/Monit.xml` (+ forms + Api)

- [ ] **Step 1: Vendor the real files** into `backend/tests/fixtures/opnsense_catalog/real/` preserving the `src/opnsense/mvc/app/...` subpaths (so `discover_models` finds them). Download each via:
```bash
curl -sL "https://raw.githubusercontent.com/opnsense/core/26.1.8/src/opnsense/mvc/app/models/OPNsense/IDS/IDS.xml" -o <fixture path>
```
(Repeat for Unbound, Monit, their `forms/*.xml`, and one `Api/*Controller.php` each.)

- [ ] **Step 2: Write the test**

```python
# backend/tests/test_catalog_real_pilot.py
from pathlib import Path

from tools.opnsense_catalog.cli import _generate

_REAL = Path(__file__).parent / "fixtures/opnsense_catalog/real"


def test_three_real_models_parse_richly():
    cat = _generate("community", "26.1.8", _REAL)
    assert set(cat["models"]) >= {"ids", "unbound", "monit"}
    # generality check: across the pilot, the MAJORITY of fields must be richly typed, not raw.
    total = sum(len(m["fields"]) for m in cat["models"].values())
    raw = sum(1 for m in cat["models"].values() for f in m["fields"] if f.get("confidence") == "raw")
    assert total > 20 and raw / total < 0.4          # <40% raw across real modules
    ids = cat["models"]["ids"]
    assert ids["endpoints"]["set"] == "ids/settings/set"
```

- [ ] **Step 3: Run the test; iterate the parser if `raw` ratio is too high**

Run: `cd backend && .venv/bin/python -m pytest tests/test_catalog_real_pilot.py -q`
Expected: PASS. If it FAILS on the raw ratio, inspect which field classes fell through (`_TYPE_MAP` misses) and add them to `model_parser._TYPE_MAP` — re-run. (This is the deliberate hardening loop; the never-drop fallback means it never crashes, only lowers the rich ratio.)

- [ ] **Step 4: Write the regeneration README**

```markdown
# backend/tools/opnsense_catalog/README.md
Generate a full catalog for a version (network):

    cd backend && .venv/bin/python -m tools.opnsense_catalog.cli generate \
        --edition community --version 26.1.8 --fetch --out ../catalog/community/26.1.8.json

Diff two versions:

    .venv/bin/python -m tools.opnsense_catalog.cli diff \
        ../catalog/community/26.1.7.json ../catalog/community/26.1.8.json

Coverage (rich vs raw) is printed after `generate`. Plugins: run the same with the plugins repo
tree under --source (extract `opnsense/plugins`); proprietary/Business plugins are out of scope.
```

- [ ] **Step 5: Commit**

```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/tests/fixtures/opnsense_catalog/real backend/tests/test_catalog_real_pilot.py backend/tools/opnsense_catalog/README.md
git commit -m "test(catalog): real 3-model pilot + coverage gate + regen docs"
```

---

## Final wiring & PR

- [ ] Run the full catalog suite once more: `cd backend && .venv/bin/python -m pytest tests/test_catalog_*.py -q` (all green) + `.venv/bin/ruff check tools/opnsense_catalog tests/test_catalog_*.py`.
- [ ] Confirm `tools/` is import-discoverable in tests: tests import `tools.opnsense_catalog.…`; if pytest can't resolve it, add `tools` next to `app` on the path — verify `backend/pyproject.toml`/`pytest.ini` `pythonpath`/`rootdir` includes `backend` (it does for `app`; `tools` sits beside it). If needed, add `pythonpath = .` under `[tool.pytest.ini_options]`.
- [ ] Open a PR to `main` (protected); poll CI green; squash-merge. This sub-project ships the generator only — no app/runtime change yet.

---

## Self-review notes (author)

- **Spec coverage:** Fetcher (T9), Model parser incl. never-drop + ArrayField grids (T2/T3), Form parser (T4), Endpoint resolver convention+PHP (T5), Discover (T6), Emitter + coverage report (T7), Differ (T8), CLI generate/diff (T10), complete-core path + 3-model real pilot + coverage gate + regen docs (T11), JSON shape matches the spec's example (model_root/xml_path/endpoints/pages/fields/grids/confidence). Plugins covered via the same `--source` over an `opnsense/plugins` tree (T11 README) — full plugin automation is the documented fast-follow. Business/dynamic-options/apply-engine/UI are explicitly out of scope per the spec.
- **Type consistency:** `Field`/`Grid`/`Model`/`ParsedModel` names + fields are used identically across T1–T11; `parse_model -> ParsedModel`, `resolve_endpoints -> (endpoints, grid_endpoints, confidence)`, `assemble_model(...) -> Model`, `build_catalog(...) -> dict`, `diff_catalogs(a,b) -> dict` are stable throughout.
- **No placeholders:** every step has runnable code/commands. The only intentionally manual step is vendoring real fixtures in T11 (with exact curl URLs).
