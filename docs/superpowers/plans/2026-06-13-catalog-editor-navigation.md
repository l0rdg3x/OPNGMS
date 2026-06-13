# Catalog Editor Navigation (sub-project 3b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the editor's flat model list into the OPNsense-like menu tree (rebuilt from harvested `Menu.xml`) with global search, and render `ref`/`enum` fields as live dropdowns from the values the device already returns.

**Architecture:** The generator harvests every module's `Menu.xml`, merges them into one tree, resolves each leaf's `url` to a catalog model id, and embeds the tree as `catalog["menu"]`. The live model endpoint (3a) also returns the available options per field (already present in the device `get` as option-dicts). The frontend replaces the flat list with a recursive menu tree (mapped leaves open the 3a model form; unmapped leaves grey + deep-link the device WebGUI) and threads live options into the field inputs.

**Tech Stack:** Backend/generator — Python 3.14, defusedxml, pytest. Frontend — React 19, Mantine v9, vitest + Testing Library + MSW.

**Spec:** `docs/superpowers/specs/2026-06-13-catalog-editor-navigation-design.md`

**Branch:** `feat/catalog-editor-navigation` (already checked out; the spec commit is already on it).

**No schema regen needed:** 3b adds NO new endpoints/params — the menu rides inside the existing `GET …/catalog` body and options inside the existing `GET …/catalog/models/{id}` body, both typed loosely (`as`) on the frontend. Do NOT run `gen:api`.

---

## Conventions

- Backend/generator from `backend/`: `cd backend && .venv/bin/pytest tests/<f>.py -q` (DB env only needed for the api test). Lint `.venv/bin/ruff check app/ tools/`.
- Frontend from `frontend/`: `npm run test -- <path>`, `npm run build`, `npm run lint`. Don't pipe test/build through `tail` (it masks the exit code).
- Commit after each task. English in code/commits.
- **Fixed shapes** (do not deviate):
  - Menu node (in `catalog["menu"]`, a list of category nodes):
    ```json
    {"id": "Services", "label": "Services", "order": 50,
     "children": [
       {"id": "Unbound", "label": "Unbound DNS", "icon": "fa fa-tags fa-fw", "order": 0,
        "children": [
          {"id": "General", "label": "General", "order": 10, "url": "/ui/unbound/general", "model_id": "unbound"},
          {"id": "Statistics", "label": "Statistics", "order": 90, "url": "/ui/diagnostics/log/...", "model_id": null}
        ]}
     ]}
    ```
    A node with a `url` and no `children` is a **leaf/page**; `model_id` is set only on leaves (resolved or `null`). `icon`/`url` present only when the source had them.
  - Live model endpoint gains `field_options: {path: [{value,label}]}` and `grid_field_options: {grid_path: {field_path: [{value,label}]}}` (empty when unreachable/read-only).

---

## Phase A — Generator: harvest `Menu.xml`

### Task A1: `menu.parse_menu` (one fragment → nodes)

**Files:**
- Create: `tools/opnsense_catalog/menu.py`
- Test: `tests/test_catalog_menu.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_menu.py
from tools.opnsense_catalog.menu import parse_menu

_FRAG = """
<menu>
  <Services>
    <Unbound VisibleName="Unbound DNS" cssClass="fa fa-tags fa-fw">
      <General order="10" url="/ui/unbound/general"/>
      <ACL VisibleName="Access Lists" order="40" url="/ui/unbound/acl"/>
    </Unbound>
  </Services>
</menu>
"""


def test_parse_menu_builds_nodes():
    nodes = parse_menu(_FRAG)
    assert len(nodes) == 1
    services = nodes[0]
    assert services["id"] == "Services" and services["label"] == "Services"
    unbound = services["children"][0]
    assert unbound["id"] == "Unbound" and unbound["label"] == "Unbound DNS"
    assert unbound["icon"] == "fa fa-tags fa-fw"
    general = unbound["children"][0]
    assert general["url"] == "/ui/unbound/general" and general["order"] == 10
    acl = unbound["children"][1]
    assert acl["label"] == "Access Lists" and "children" not in acl  # leaf
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_menu.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'tools.opnsense_catalog.menu'`

- [ ] **Step 3: Write minimal implementation**

```python
# tools/opnsense_catalog/menu.py
from __future__ import annotations

from defusedxml import ElementTree as DET

_ORDER_LAST = 10_000  # nodes without an explicit order sort after those with one


def _node(el) -> dict:
    label = (el.get("VisibleName") or el.tag).strip()
    order = el.get("order")
    node: dict = {"id": el.tag, "label": label,
                  "order": int(order) if order and order.isdigit() else _ORDER_LAST}
    css = el.get("cssClass")
    if css:
        node["icon"] = css.strip()
    url = el.get("url")
    if url:
        node["url"] = url.strip()
    children = [_node(c) for c in list(el)]
    if children:
        node["children"] = children
    return node


def parse_menu(xml_text: str) -> list[dict]:
    """One <menu> fragment -> a list of top-level category nodes (recursive)."""
    root = DET.fromstring(xml_text)
    return [_node(c) for c in list(root)]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_menu.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add tools/opnsense_catalog/menu.py tests/test_catalog_menu.py
git commit -m "feat(catalog): menu.parse_menu — one Menu.xml fragment to nodes"
```

---

### Task A2: `menu.merge_menus` (deep-merge fragments by id-path)

**Files:**
- Modify: `tools/opnsense_catalog/menu.py`
- Test: `tests/test_catalog_menu.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_menu.py
from tools.opnsense_catalog.menu import merge_menus

_A = parse_menu("""
<menu><Services><Unbound VisibleName="Unbound DNS"><General order="10" url="/ui/unbound/general"/></Unbound></Services></menu>
""")
_B = parse_menu("""
<menu><Services><IDS VisibleName="Intrusion Detection"><Admin order="10" url="/ui/ids"/></IDS></Services></menu>
""")


def test_merge_unions_under_same_category_sorted():
    merged = merge_menus([_A, _B])
    assert [c["id"] for c in merged] == ["Services"]
    groups = merged[0]["children"]
    assert sorted(g["id"] for g in groups) == ["IDS", "Unbound"]


def test_merge_keeps_existing_label_does_not_overwrite():
    # a second fragment with the same id but a bare tag label must NOT clobber a real VisibleName
    plain = parse_menu("<menu><Services><Unbound><Advanced order='30' url='/ui/unbound/advanced'/></Unbound></Services></menu>")
    merged = merge_menus([_A, plain])
    unbound = merged[0]["children"][0]
    assert unbound["label"] == "Unbound DNS"  # kept from _A, not "Unbound"
    assert [c["id"] for c in unbound["children"]] == ["General", "Advanced"]  # children unioned, order-sorted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_menu.py -k merge -q`
Expected: FAIL — `ImportError: cannot import name 'merge_menus'`

- [ ] **Step 3: Write minimal implementation (append to menu.py)**

```python
# append to tools/opnsense_catalog/menu.py
def _merge_lists(lists: list[list[dict]]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for nodes in lists:
        for n in nodes:
            if n["id"] not in by_id:
                by_id[n["id"]] = {"id": n["id"], "label": n["id"], "order": _ORDER_LAST}
                order.append(n["id"])
            cur = by_id[n["id"]]
            # Prefer a real VisibleName (label != tag) over a bare-tag label; never clobber.
            if cur["label"] == cur["id"] and n["label"] != n["id"]:
                cur["label"] = n["label"]
            if "icon" not in cur and "icon" in n:
                cur["icon"] = n["icon"]
            if "url" not in cur and "url" in n:
                cur["url"] = n["url"]
            if n.get("order", _ORDER_LAST) < cur["order"]:
                cur["order"] = n["order"]
            cur.setdefault("_kids", []).append(n.get("children", []))
    out = []
    for cid in order:
        node = by_id[cid]
        kids = node.pop("_kids", [])
        merged_kids = _merge_lists(kids)
        if merged_kids:
            node["children"] = merged_kids
        out.append(node)
    out.sort(key=lambda n: (n["order"], n["label"]))
    return out


def merge_menus(fragments: list[list[dict]]) -> list[dict]:
    """Deep-merge parsed fragments into one tree (union children by id, sort by order then label)."""
    return _merge_lists(fragments)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_menu.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add tools/opnsense_catalog/menu.py tests/test_catalog_menu.py
git commit -m "feat(catalog): menu.merge_menus — deep-merge fragments by id-path"
```

---

### Task A3: `menu.resolve_model_ids` (leaf url → catalog model id)

**Files:**
- Modify: `tools/opnsense_catalog/menu.py`
- Test: `tests/test_catalog_menu.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_menu.py
from tools.opnsense_catalog.menu import resolve_model_ids


def test_resolve_model_ids_maps_leaves():
    menu = parse_menu("""
    <menu><Services>
      <Unbound><General order="10" url="/ui/unbound/general"/></Unbound>
      <Firewall><Alias order="10" url="/ui/firewall/alias"/></Firewall>
      <IDS><Log order="90" url="/ui/diagnostics/log/core/suricata"/></IDS>
    </Services></menu>""")
    resolved = resolve_model_ids(menu, {"unbound", "firewall.alias"})
    services = resolved[0]["children"]
    by_id = {g["id"]: g for g in services}
    assert by_id["Unbound"]["children"][0]["model_id"] == "unbound"     # /ui/unbound/general -> unbound
    assert by_id["Firewall"]["children"][0]["model_id"] == "firewall.alias"  # <a>.<b> match
    assert by_id["IDS"]["children"][0]["model_id"] is None              # diagnostics -> no model
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_menu.py -k resolve -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_model_ids'`

- [ ] **Step 3: Write minimal implementation (append to menu.py)**

```python
# append to tools/opnsense_catalog/menu.py
def _resolve_leaf(url: str, model_ids: set[str]) -> str | None:
    parts = [p for p in url.split("/") if p]
    if not parts or parts[0] != "ui" or len(parts) < 2:
        return None
    seg = parts[1:]  # after /ui/
    candidates = []
    if len(seg) >= 2:
        candidates.append(f"{seg[0]}.{seg[1]}")
    candidates.append(seg[0])
    for c in candidates:
        if c in model_ids:
            return c
    return None


def resolve_model_ids(menu: list[dict], model_ids: set[str]) -> list[dict]:
    """Set `model_id` on every leaf (a node with `url` and no children); recurse. Returns the menu."""
    for node in menu:
        if "children" in node:
            resolve_model_ids(node["children"], model_ids)
        elif node.get("url"):
            node["model_id"] = _resolve_leaf(node["url"], model_ids)
    return menu
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_menu.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add tools/opnsense_catalog/menu.py tests/test_catalog_menu.py
git commit -m "feat(catalog): menu.resolve_model_ids — map leaf url to catalog model"
```

---

### Task A4: Wire menu harvest into generation + fixture

**Files:**
- Modify: `tools/opnsense_catalog/menu.py` (add `discover_menus`)
- Modify: `tools/opnsense_catalog/cli.py` (`_generate`)
- Modify: `tools/opnsense_catalog/emit.py` (`coverage_report` menu counts)
- Create: `tests/fixtures/opnsense_catalog/minicore/src/opnsense/mvc/app/models/OPNsense/IDS/Menu/Menu.xml`
- Test: `tests/test_catalog_cli.py` (add)

- [ ] **Step 1: Create the fixture Menu.xml**

`tests/fixtures/opnsense_catalog/minicore/src/opnsense/mvc/app/models/OPNsense/IDS/Menu/Menu.xml`:
```xml
<menu>
  <Services>
    <IDS VisibleName="Intrusion Detection" cssClass="fa fa-shield fa-fw">
      <Administration order="10" url="/ui/ids"/>
      <Log VisibleName="Log File" order="90" url="/ui/diagnostics/log/core/suricata"/>
    </IDS>
  </Services>
</menu>
```

- [ ] **Step 2: Write the failing test (append to tests/test_catalog_cli.py)**

```python
# append to tests/test_catalog_cli.py
def test_generate_emits_resolved_menu(tmp_path):
    out = tmp_path / "26.1.8.json"
    rc = main(["generate", "--edition", "community", "--version", "26.1.8",
               "--source", str(_FIX), "--out", str(out)])
    assert rc == 0
    cat = json.loads(out.read_text())
    menu = cat["menu"]
    services = next(c for c in menu if c["id"] == "Services")
    ids = next(g for g in services["children"] if g["id"] == "IDS")
    admin = next(p for p in ids["children"] if p["id"] == "Administration")
    assert admin["model_id"] == "ids"          # /ui/ids -> model 'ids'
    log = next(p for p in ids["children"] if p["id"] == "Log")
    assert log["model_id"] is None             # diagnostics -> unmapped
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_cli.py -k menu -q`
Expected: FAIL — `KeyError: 'menu'`

- [ ] **Step 4: Write the implementation**

Add `discover_menus` to `menu.py`:
```python
# append to tools/opnsense_catalog/menu.py
from pathlib import Path


def discover_menus(root: Path) -> list[Path]:
    """All module Menu.xml files under an extracted source tree."""
    return sorted(root.rglob("mvc/app/models/OPNsense/*/Menu/Menu.xml"))
```

In `cli.py`, import and set the menu in `_generate` (after `build_catalog`):
```python
# tools/opnsense_catalog/cli.py — add to imports
from tools.opnsense_catalog.menu import discover_menus, merge_menus, parse_menu, resolve_model_ids
```
Change the end of `_generate` to build + attach the menu:
```python
    cat = build_catalog(models, edition=edition, version=version,
                        generated_from={"core": version})
    fragments = [parse_menu(p.read_text()) for p in discover_menus(source)]
    cat["menu"] = resolve_model_ids(merge_menus(fragments), set(cat["models"]))
    return cat
```

In `emit.py`, extend `coverage_report` with menu counts:
```python
def coverage_report(catalog: dict) -> dict:
    total = raw = 0
    for m in catalog["models"].values():
        for fl in [m["fields"], *[g["fields"] for g in m.get("grids", [])]]:
            for f in fl:
                total += 1
                raw += 1 if f.get("confidence") == "raw" else 0
    leaves = unmapped = 0

    def _walk(nodes):
        nonlocal leaves, unmapped
        for n in nodes:
            if n.get("children"):
                _walk(n["children"])
            elif n.get("url"):
                leaves += 1
                unmapped += 1 if n.get("model_id") is None else 0

    _walk(catalog.get("menu", []))
    return {"models": len(catalog["models"]), "fields_total": total, "fields_raw": raw,
            "menu_leaves": leaves, "menu_unmapped": unmapped}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_cli.py tests/test_catalog_emit.py -q`
Expected: PASS. (`test_catalog_emit.py` only reads `rep["models"]`/`fields_total`/`fields_raw`, so the
two new `coverage_report` keys don't break it; its catalog has no menu → `menu_leaves == 0`.) Then
`.venv/bin/ruff check tools/`.

- [ ] **Step 6: Commit**

```bash
cd backend && git add tools/opnsense_catalog/menu.py tools/opnsense_catalog/cli.py tools/opnsense_catalog/emit.py tests/test_catalog_cli.py tests/fixtures/opnsense_catalog/minicore/
git commit -m "feat(catalog): harvest Menu.xml into catalog[menu] + coverage counts"
```

---

## Phase B — Backend: live options on the model endpoint

### Task B1: `catalog_live.extract_options` (scalar field options)

**Files:**
- Modify: `app/services/catalog_live.py`
- Test: `tests/test_catalog_live.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_live.py
from app.services.catalog_live import extract_options

_REF_MODEL = {
    "model_root": "unbound",
    "fields": [
        {"path": "general.outgoing", "type": "ref"},
        {"path": "general.port", "type": "int"},
    ],
}


def test_extract_options_returns_choices_for_option_dict_fields():
    get_response = {"unbound": {"general": {
        "outgoing": {"lan": {"value": "LAN", "selected": "1"}, "wan": {"value": "WAN", "selected": "0"}},
        "port": "53",
    }}}
    opts = extract_options(get_response, _REF_MODEL)
    assert opts["general.outgoing"] == [{"value": "lan", "label": "LAN"}, {"value": "wan", "label": "WAN"}]
    assert "general.port" not in opts  # plain string -> no options
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -k extract_options -q`
Expected: FAIL — `ImportError: cannot import name 'extract_options'`

- [ ] **Step 3: Write minimal implementation (append to catalog_live.py)**

```python
# app/services/catalog_live.py — add to the import line
from app.services.opnsense_values import is_option_dict, options, selected
```
(extend the existing `from app.services.opnsense_values import is_option_dict, selected` to also import `options`)

```python
# append to app/services/catalog_live.py
def extract_options(get_response: dict, model: dict) -> dict[str, list[dict]]:
    """{field_path: [{value, label}]} for scalar fields the device renders as an option-dict
    (enum/ref/interface). The live choices the editor needs for dropdowns."""
    root = (get_response or {}).get(model.get("model_root", ""), {})
    field_paths = {f["path"] for f in model.get("fields", [])}
    out: dict[str, list[dict]] = {}

    def walk(node, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        for key, val in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if is_option_dict(val):
                if path in field_paths:
                    out[path] = options(val)
            elif isinstance(val, dict):
                walk(val, path)

    walk(root, "")
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_live.py tests/test_catalog_live.py
git commit -m "feat(catalog): catalog_live.extract_options — live dropdown choices per field"
```

---

### Task B2: `catalog_live.extract_grid_options` (grid-cell options)

**Files:**
- Modify: `app/services/catalog_live.py`
- Test: `tests/test_catalog_live.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_live.py
from app.services.catalog_live import extract_grid_options

_GRID_OPT = {"path": "hosts", "fields": [{"path": "rr", "type": "enum"}, {"path": "hostname", "type": "string"}]}


def test_extract_grid_options_returns_cell_choices():
    get_response = {"unbound": {"hosts": {
        "ab-12": {"rr": {"A": {"value": "A", "selected": "1"}, "AAAA": {"value": "AAAA", "selected": "0"}},
                  "hostname": "web"},
    }}}
    out = extract_grid_options(get_response, _MODEL, _GRID_OPT)
    assert out["rr"] == [{"value": "A", "label": "A"}, {"value": "AAAA", "label": "AAAA"}]
    assert "hostname" not in out  # plain string cell -> no options
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -k grid_options -q`
Expected: FAIL — `ImportError: cannot import name 'extract_grid_options'`

- [ ] **Step 3: Write minimal implementation (append to catalog_live.py)**

```python
# append to app/services/catalog_live.py
def extract_grid_options(get_response: dict, model: dict, grid: dict) -> dict[str, list[dict]]:
    """{field_path: [{value, label}]} for a grid's cells the device renders as option-dicts.

    Reads the FIRST existing row's cells for the choices (the option set is identical across rows).
    """
    root = (get_response or {}).get(model.get("model_root", ""), {})
    node = root
    for part in grid["path"].split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    out: dict[str, list[dict]] = {}
    if not isinstance(node, dict):
        return out
    field_paths = [f["path"] for f in grid.get("fields", [])]
    for cells in node.values():
        if not isinstance(cells, dict):
            continue
        for fp in field_paths:
            if fp not in out and is_option_dict(cells.get(fp)):
                out[fp] = options(cells[fp])
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_live.py tests/test_catalog_live.py
git commit -m "feat(catalog): catalog_live.extract_grid_options — live grid-cell choices"
```

---

### Task B3: Model endpoint returns `field_options` + `grid_field_options`

**Files:**
- Modify: `app/api/catalog.py` (`read_catalog_model`)
- Test: `tests/test_catalog_api.py` (add)

- [ ] **Step 1: Write the failing test (append to tests/test_catalog_api.py)**

```python
# append to tests/test_catalog_api.py
@respx.mock
async def test_read_model_returns_live_options(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    payload = {"unbound": {
        "general": {"enabled": "1",
                    "outgoing": {"lan": {"value": "LAN", "selected": "1"}}},
        "hosts": {"ab": {"hostname": "web", "server": "10.0.0.10"}},
    }}
    respx.get("https://203.0.113.10/api/unbound/settings/get").mock(
        return_value=Response(200, json=payload))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid, base_url="https://203.0.113.10")
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/unbound", headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["field_options"]["general.outgoing"] == [{"value": "lan", "label": "LAN"}]
    assert "grid_field_options" in body  # present (may be empty for this model's plain-string grid)
```

> The `_CATALOG` fixture's `unbound` model has fields `general.enabled` (bool) + a grid `hosts`. Add a
> `ref` field `general.outgoing` to that model in the existing `_CATALOG` so the option survives the
> `field_paths` filter: in `_CATALOG["models"]["unbound"]["fields"]`, append
> `{"path": "general.outgoing", "type": "ref"}`. (The 3a tests don't assert the field list exhaustively,
> so this is additive.) The `_device` helper already accepts `base_url` (added in 3a Task A4).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_api.py -k live_options -q` (DB env set)
Expected: FAIL — `KeyError: 'field_options'`

- [ ] **Step 3: Write the implementation**

In `app/api/catalog.py`, import the new helpers (extend the existing catalog_live import):
```python
from app.services.catalog_live import (
    extract_grid_options, extract_grid_rows, extract_options, flatten_values,
)
```
Update the read_only short-circuit + the success block to include the new keys. Change the `base` dict:
```python
    base = {"model": model, "values": {}, "grids": {}, "field_options": {}, "grid_field_options": {},
            "reachable": False, "read_only": model_id in CATALOG_DENYLIST}
    if base["read_only"]:
        return base
```
and after `base["grids"] = {...}` add:
```python
    base["field_options"] = extract_options(raw, model)
    base["grid_field_options"] = {
        g["path"]: extract_grid_options(raw, model, g) for g in model.get("grids", [])}
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_api.py -q` (DB env set)
Expected: PASS. Then `.venv/bin/ruff check app/`.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/api/catalog.py tests/test_catalog_api.py
git commit -m "feat(catalog): model endpoint returns live field_options + grid_field_options"
```

---

## Phase C — Frontend: menu tree + live dropdowns

### Task C1: Types + i18n

**Files:**
- Modify: `frontend/src/catalog/catalogTypes.ts`
- Modify: `frontend/src/i18n/en.ts`

- [ ] **Step 1: Add the menu + options types**

Append to `catalogTypes.ts`:
```ts
export type MenuNode = {
  id: string;
  label: string;
  order: number;
  icon?: string;
  url?: string;
  model_id?: string | null;
  children?: MenuNode[];
};
```
Extend `CatalogModelLive` (add two fields):
```ts
export type CatalogModelLive = {
  model: CatalogModel;
  values: Record<string, string | string[]>;
  grids: Record<string, GridRow[]>;
  field_options: Record<string, { value: string; label: string }[]>;
  grid_field_options: Record<string, Record<string, { value: string; label: string }[]>>;
  reachable: boolean;
  read_only: boolean;
};
```

Add to `en.ts` `catalog` block:
```ts
    openWebgui: "Open in WebGUI",
    searchAll: "Search settings…",
```

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc -b`
Expected: PASS (the existing model endpoint cast `as CatalogModelLive` now requires the two fields — the B3 endpoint provides them; the hook still casts, so this compiles).

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/catalog/catalogTypes.ts src/i18n/en.ts
git commit -m "feat(catalog): menu + live-option types + nav i18n"
```

---

### Task C2: `useDeviceCatalog` returns the menu

**Files:**
- Modify: `frontend/src/catalog/catalogHooks.ts`
- Test: `frontend/src/catalog/__tests__/catalogHooks.test.tsx` (add)

- [ ] **Step 1: Write the failing test (append)**

```tsx
// append to src/catalog/__tests__/catalogHooks.test.tsx
import { useDeviceCatalog } from "../catalogHooks";

describe("useDeviceCatalog", () => {
  it("returns the menu tree", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog", () =>
        HttpResponse.json({
          resolved_version: "26.1.8", models: {},
          menu: [{ id: "Services", label: "Services", order: 50,
                   children: [{ id: "Unbound", label: "Unbound DNS", order: 0,
                                children: [{ id: "General", label: "General", order: 10,
                                             url: "/ui/unbound/general", model_id: "unbound" }] }] }],
        })),
    );
    const { result } = renderHook(() => useDeviceCatalog("d1"), { wrapper });
    await waitFor(() => expect(result.current.data?.menu?.[0].id).toBe("Services"));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogHooks.test.tsx`
Expected: FAIL — `menu` not on the returned type / undefined.

- [ ] **Step 3: Implement**

In `catalogHooks.ts`, import `MenuNode` and widen `useDeviceCatalog`'s return type + cast:
```ts
import type { CatalogChangeBody, CatalogModel, CatalogModelLive, MenuNode } from "./catalogTypes";
```
Change `useDeviceCatalog`'s `queryFn` return type to
`Promise<{ resolved_version: string; models: Record<string, CatalogModel>; menu?: MenuNode[] }>`
and the cast to match (`as { resolved_version: string; models: Record<string, CatalogModel>; menu?: MenuNode[] }`).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogHooks.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/catalogHooks.ts src/catalog/__tests__/catalogHooks.test.tsx
git commit -m "feat(catalog): useDeviceCatalog exposes the menu tree"
```

---

### Task C3: `CatalogMenuTree` (recursive nav + search + deep-link)

**Files:**
- Create: `frontend/src/catalog/CatalogMenuTree.tsx`
- Test: `frontend/src/catalog/__tests__/catalogMenuTree.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogMenuTree.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogMenuTree } from "../CatalogMenuTree";
import type { MenuNode } from "../catalogTypes";

const MENU: MenuNode[] = [
  { id: "Services", label: "Services", order: 50, children: [
    { id: "Unbound", label: "Unbound DNS", order: 0, children: [
      { id: "General", label: "General", order: 10, url: "/ui/unbound/general", model_id: "unbound" },
      { id: "Stats", label: "Statistics", order: 90, url: "/ui/diagnostics/x", model_id: null },
    ]},
  ]},
];

describe("CatalogMenuTree", () => {
  it("selects a mapped leaf's model", () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <CatalogMenuTree nodes={MENU} baseUrl="https://1.2.3.4" search="" selected={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("General"));
    expect(onSelect).toHaveBeenCalledWith("unbound");
  });

  it("renders an unmapped leaf as a WebGUI deep-link", () => {
    renderWithProviders(
      <CatalogMenuTree nodes={MENU} baseUrl="https://1.2.3.4" search="" selected={null} onSelect={() => {}} />);
    const link = screen.getByTestId("catalog-menu-link-Stats");
    expect(link).toHaveAttribute("href", "https://1.2.3.4/ui/diagnostics/x");
  });

  it("filters by search (hides non-matching leaves)", () => {
    renderWithProviders(
      <CatalogMenuTree nodes={MENU} baseUrl="https://1.2.3.4" search="statistics" selected={null} onSelect={() => {}} />);
    expect(screen.queryByText("General")).toBeNull();
    expect(screen.getByText("Statistics")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogMenuTree.test.tsx`
Expected: FAIL — cannot find `../CatalogMenuTree`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/catalog/CatalogMenuTree.tsx
import { NavLink } from "@mantine/core";
import { useT } from "../i18n";
import type { MenuNode } from "./catalogTypes";

/** Validated WebGUI deep-link (http(s) base only), else undefined (mirrors DeviceActions). */
function deepLink(baseUrl: string, url: string): string | undefined {
  if (!/^https?:\/\//i.test(baseUrl)) return undefined;
  return baseUrl.replace(/\/$/, "") + url;
}

function matches(node: MenuNode, q: string): boolean {
  if (!q) return true;
  if (node.label.toLowerCase().includes(q) || (node.url ?? "").toLowerCase().includes(q)) return true;
  return (node.children ?? []).some((c) => matches(c, q));
}

export function CatalogMenuTree({
  nodes, baseUrl, search, selected, onSelect,
}: {
  nodes: MenuNode[];
  baseUrl: string;
  search: string;
  selected: string | null;
  onSelect: (modelId: string) => void;
}) {
  const t = useT();
  const q = search.trim().toLowerCase();
  return (
    <>
      {nodes.filter((n) => matches(n, q)).map((node) => {
        if (node.children && node.children.length > 0) {
          return (
            <NavLink key={node.id} label={node.label} defaultOpened={!!q}
              leftSection={node.icon ? <i className={node.icon} /> : null}>
              <CatalogMenuTree nodes={node.children} baseUrl={baseUrl} search={search}
                selected={selected} onSelect={onSelect} />
            </NavLink>
          );
        }
        if (node.model_id) {
          return (
            <NavLink key={node.id} label={node.label} active={selected === node.model_id}
              onClick={() => onSelect(node.model_id!)} />
          );
        }
        const href = node.url ? deepLink(baseUrl, node.url) : undefined;
        return (
          <NavLink key={node.id} label={node.label} disabled={!href}
            component="a" href={href} target="_blank" rel="noreferrer"
            data-testid={`catalog-menu-link-${node.id}`}
            description={href ? t.catalog.openWebgui : undefined} />
        );
      })}
    </>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogMenuTree.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogMenuTree.tsx src/catalog/__tests__/catalogMenuTree.test.tsx
git commit -m "feat(catalog): CatalogMenuTree — recursive nav + search + WebGUI deep-link"
```

---

### Task C4: `CatalogFieldInput` — live options

**Files:**
- Modify: `frontend/src/catalog/CatalogFieldInput.tsx`
- Test: `frontend/src/catalog/__tests__/catalogFieldInput.test.tsx` (add)

- [ ] **Step 1: Write the failing test (append)**

```tsx
// append to src/catalog/__tests__/catalogFieldInput.test.tsx
it("renders a select from liveOptions for a ref field", () => {
  renderWithProviders(
    <CatalogFieldInput
      field={{ path: "general.outgoing", type: "ref" }}
      value="lan" liveOptions={[{ value: "lan", label: "LAN" }, { value: "wan", label: "WAN" }]}
      onChange={() => {}} disabled={false} />,
  );
  // a Select renders an input with the chosen option's label
  expect(screen.getByTestId("catalog-field-general.outgoing")).toBeInTheDocument();
});

it("falls back to text for a ref with no liveOptions", () => {
  renderWithProviders(
    <CatalogFieldInput field={{ path: "general.outgoing", type: "ref" }}
      value="x" onChange={() => {}} disabled={false} />);
  expect(screen.getByTestId("catalog-field-general.outgoing")).toHaveValue("x");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogFieldInput.test.tsx`
Expected: FAIL — `liveOptions` not a prop; ref renders as text always.

- [ ] **Step 3: Implement**

In `CatalogFieldInput.tsx`, add the prop + a live-options branch (before the enum/multienum branches):
```tsx
export function CatalogFieldInput({
  field, value, onChange, disabled, liveOptions,
}: {
  field: CatalogField;
  value: string;
  onChange: (path: string, value: string) => void;
  disabled: boolean;
  liveOptions?: { value: string; label: string }[];
}) {
  const label = field.label || field.path;
  const testid = `catalog-field-${field.path}`;
  // Live dropdown: prefer device-provided options for ref/enum/multienum.
  const live = liveOptions && liveOptions.length > 0 ? liveOptions : null;
  const options = live ?? (field.options ?? []).map((o) => ({ value: o, label: o }));
  // ... bool / int branches unchanged ...
```
Then change the `enum`/`multienum`/`ref` handling: render a `Select` when `field.type === "enum"` OR (`field.type === "ref"` AND `live`); a `MultiSelect` when `field.type === "multienum"`; else the existing text input. Concretely, replace the `enum` branch condition with:
```tsx
  if (field.type === "enum" || (field.type === "ref" && live)) {
    return (
      <Select label={label} data={options} data-testid={testid} disabled={disabled}
        value={value} onChange={(v) => onChange(field.path, v ?? "")} />
    );
  }
```
(the `multienum` and trailing text branches stay as-is; a `ref` without `live` falls through to the text input).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogFieldInput.test.tsx`
Expected: PASS (existing 3 + new 2)

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogFieldInput.tsx src/catalog/__tests__/catalogFieldInput.test.tsx
git commit -m "feat(catalog): CatalogFieldInput — live dropdown options for ref/enum"
```

---

### Task C5: `CatalogModelForm` + `CatalogGridTable` thread live options

**Files:**
- Modify: `frontend/src/catalog/CatalogModelForm.tsx`
- Modify: `frontend/src/catalog/CatalogGridTable.tsx`
- Test: `frontend/src/catalog/__tests__/catalogModelForm.test.tsx` (add)

- [ ] **Step 1: Write the failing test (append)**

```tsx
// append to src/catalog/__tests__/catalogModelForm.test.tsx
it("renders a ref field as a live dropdown from field_options", () => {
  const live: CatalogModelLive = {
    model: { id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
             fields: [{ path: "general.outgoing", type: "ref" }], grids: [],
             pages: [{ id: "general", fields: ["general.outgoing"] }] },
    values: { "general.outgoing": "lan" }, grids: {},
    field_options: { "general.outgoing": [{ value: "lan", label: "LAN" }] },
    grid_field_options: {}, reachable: true, read_only: false,
  };
  renderWithProviders(<CatalogModelForm live={live} onPropose={() => Promise.resolve()} />);
  // the Mantine Select renders the chosen label
  expect(screen.getByTestId("catalog-field-general.outgoing")).toBeInTheDocument();
});
```

> The existing `LIVE` fixture in this file lacks `field_options`/`grid_field_options`. Add
> `field_options: {}, grid_field_options: {}` to it so it satisfies the extended `CatalogModelLive` type.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogModelForm.test.tsx`
Expected: FAIL — type error (missing fields) / options not threaded.

- [ ] **Step 3: Implement**

In `CatalogModelForm.tsx`, destructure the new fields and pass them down:
```tsx
  const { model, values, grids, field_options, grid_field_options, reachable, read_only } = live;
```
In the scalar `CatalogFieldInput` render, add `liveOptions={field_options[path]}`:
```tsx
              <CatalogFieldInput key={path} field={f} value={work[path] ?? ""} disabled={!editable}
                liveOptions={field_options[path]}
                onChange={(p, v) => setWork((w) => ({ ...w, [p]: v }))} />
```
In the grid render, pass the grid's options:
```tsx
          <CatalogGridTable grid={g} rows={grids[g.path] ?? []} disabled={!editable}
            fieldOptions={grid_field_options[g.path] ?? {}}
            onOps={(ops) => setGridOps((m) => ({ ...m, [g.path]: ops }))} />
```

In `CatalogGridTable.tsx`, accept `fieldOptions` and thread it into the row-modal inputs:
```tsx
export function CatalogGridTable({
  grid, rows, disabled, onOps, fieldOptions = {},
}: {
  grid: CatalogGrid;
  rows: GridRow[];
  disabled: boolean;
  onOps: (ops: CatalogGridOp[]) => void;
  fieldOptions?: Record<string, { value: string; label: string }[]>;
}) {
```
and in the modal's `CatalogFieldInput`, add `liveOptions={fieldOptions[f.path]}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogModelForm.test.tsx src/catalog/__tests__/catalogGridTable.test.tsx`
Expected: PASS (existing + new; the grid test's component call still works — `fieldOptions` is optional).

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogModelForm.tsx src/catalog/CatalogGridTable.tsx src/catalog/__tests__/catalogModelForm.test.tsx
git commit -m "feat(catalog): thread live field/grid options into the form inputs"
```

---

### Task C6: `CatalogEditorTab` — menu tree + baseUrl

**Files:**
- Modify: `frontend/src/catalog/CatalogEditorTab.tsx`
- Modify: `frontend/src/pages/DeviceDetailPage.tsx` (pass `baseUrl`)
- Test: `frontend/src/catalog/__tests__/catalogEditorTab.test.tsx` (replace the list assertion)

- [ ] **Step 1: Update the failing test**

Replace the body of the existing `catalogEditorTab.test.tsx` test so the catalog response includes a
`menu` and the assertion drives the tree (the flat-list `getByText("Unbound")` becomes the menu leaf):

```tsx
// src/catalog/__tests__/catalogEditorTab.test.tsx — update the CATALOG const + the test
const CATALOG = {
  resolved_version: "26.1.8",
  models: {
    unbound: { id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
               fields: [{ path: "general.enabled", type: "bool" }], grids: [],
               pages: [{ id: "general", fields: ["general.enabled"] }], read_only: false },
  },
  menu: [{ id: "Services", label: "Services", order: 50, children: [
    { id: "Unbound", label: "Unbound DNS", order: 0, children: [
      { id: "General", label: "General", order: 10, url: "/ui/unbound/general", model_id: "unbound" }]}]}],
};

it("navigates the menu tree and opens a model", async () => {
  server.use(
    http.get("*/api/tenants/t1/devices/d1/catalog", () => HttpResponse.json(CATALOG)),
    http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound", () =>
      HttpResponse.json({ model: CATALOG.models.unbound, values: { "general.enabled": "1" },
                          grids: {}, field_options: {}, grid_field_options: {},
                          reachable: true, read_only: false })),
  );
  renderWithProviders(withTenant(<CatalogEditorTab deviceId="d1" baseUrl="https://1.2.3.4" />));
  await waitFor(() => expect(screen.getByText("Services")).toBeInTheDocument());
  fireEvent.click(screen.getByText("General"));   // a menu leaf
  await waitFor(() => expect(screen.getByTestId("catalog-field-general.enabled")).toBeInTheDocument());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogEditorTab.test.tsx`
Expected: FAIL — `baseUrl` not a prop; the menu tree isn't rendered.

- [ ] **Step 3: Implement**

Rework `CatalogEditorTab` to accept `baseUrl` and render `CatalogMenuTree` instead of the flat list (drop
the `models`/flat-filter `useMemo`; keep search + selected + the model pane):
```tsx
import { CatalogMenuTree } from "./CatalogMenuTree";
// signature:
export function CatalogEditorTab({ deviceId, baseUrl }: { deviceId: string; baseUrl: string }) {
```
Left pane becomes:
```tsx
        <Stack gap="xs">
          <TextInput placeholder={t.catalog.searchAll} value={search}
            onChange={(e) => setSearch(e.currentTarget.value)} data-testid="catalog-search" />
          <ScrollArea h={500}>
            <CatalogMenuTree nodes={catalog.data.menu ?? []} baseUrl={baseUrl} search={search}
              selected={selected} onSelect={setSelected} />
          </ScrollArea>
        </Stack>
```
The empty-state guard becomes: if no `catalog.data` OR `(catalog.data.menu ?? []).length === 0` → `noModels`.
(`Badge`/`NavLink` imports for the old list can be dropped if now unused — let eslint flag them.)

In `DeviceDetailPage.tsx`, pass the device base_url to the tab:
```tsx
  {deviceId && <CatalogEditorTab deviceId={deviceId} baseUrl={device.base_url} />}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogEditorTab.test.tsx`
Expected: PASS

- [ ] **Step 5: Build gate + commit**

```bash
cd frontend && npm run build && npm run lint
git add src/catalog/CatalogEditorTab.tsx src/pages/DeviceDetailPage.tsx src/catalog/__tests__/catalogEditorTab.test.tsx
git commit -m "feat(catalog): editor uses the OPNsense-like menu tree (+ WebGUI deep-link)"
```

---

## Final verification

- [ ] **Backend + generator**

```bash
cd backend && export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" ADMIN_DATABASE_URL="$TEST_DATABASE_URL"
.venv/bin/pytest -q && .venv/bin/ruff check app/ tools/
```
Expected: all green; ruff clean.

- [ ] **Frontend**

```bash
cd frontend && npm run test && npm run build && npm run lint
```
Expected: all green.

- [ ] **Device-detail editor-tab test still passes** (the B-phase response shape changed)

The existing `src/pages/__tests__/deviceDetailEditorTab.test.tsx` only asserts the "Editor" tab exists;
the `CatalogEditorTab` now needs a `baseUrl` prop. If that test renders the real `DeviceDetailPage`
(which supplies `device.base_url`), it still works. Confirm it passes in the frontend run above; if it
renders `CatalogEditorTab` directly, add `baseUrl="https://x"`.

---

## Self-review (controller — done at plan-write time)

**Spec coverage:**
- Generator menu harvest (parse/merge/resolve + wire + coverage) → A1–A4. ✓
- Menu in `catalog["menu"]`, no new asset → A4 (`_generate`). ✓
- Live options reuse the live read (extract_options/grid_options + endpoint fields) → B1–B3. ✓
- Frontend menu tree (Category→Module→pages, icons, order), global search, mapped→model form, unmapped→greyed+WebGUI deep-link → C3 + C6. ✓
- `ref`/`enum` live dropdowns, fallback text → C4 + C5. ✓
- `baseUrl` for the deep-link from DeviceDetailPage → C6. ✓
- Testing matrix (generator, options, endpoint, tree, field input, form, tab) → covered. ✓

**Type consistency:** `MenuNode` (C1) is used identically in C2/C3/C6. `CatalogModelLive` gains `field_options`/`grid_field_options` (C1) consumed in C5 and provided by B3. `liveOptions` prop (C4) flows from C5 (`field_options[path]` + `grid_field_options[grid][field]`). `deepLink` (C3) mirrors `DeviceActions`'s http(s) guard.

**Risk flags:**
- A4: `coverage_report` gains two keys; `test_catalog_emit.py` reads only the old keys, so it stays green (verified).
- B3: the test adds a `ref` field to the shared `_CATALOG.unbound` fixture; confirm 3a tests don't assert that model's exact field list (they don't).
- C6: it rewrites the existing `catalogEditorTab.test.tsx` (flat list → tree) and adds the `baseUrl` prop — the device-detail wiring test must still pass (final verification).
- **VERIFY-ON-DEVICE (carry to bring-up):** the whole live-options feature assumes OPNsense relation/interface fields come back as option-dicts in the model `get`. Confirm against a real box; if some `ref` fields don't, they simply fall back to text (no breakage).
