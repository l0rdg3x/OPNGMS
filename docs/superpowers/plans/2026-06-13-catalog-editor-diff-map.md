# Sub-project 3c — cross-version diff badges + live config.xml map — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended)
> or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add the two final enrichments to the version-aware OPNsense config editor — cross-version diff
badges ("new/changed since version X") and a read-only live `config.xml` map cross-referenced to the
catalog — building on 3a (#97) / 3b (#98/#99).

**Architecture:** Two pure backend services (`catalog_versions.diff_catalogs`, `config_map.annotate_with_catalog`)
+ two read-only `DEVICE_VIEW` endpoints on the existing catalog router, consumed by additions to the
existing `CatalogEditorTab` (a baseline selector + per-field/model badges) and a new "Config map" view
that reuses `ConfigTree`. No mutation surface; all reads degrade safely.

**Tech Stack:** Python 3.14 / FastAPI / SQLAlchemy async; React 19 / TypeScript / Mantine v9 / TanStack
Query / Vitest. Spec: `docs/superpowers/specs/2026-06-13-catalog-editor-diff-map-design.md`.

**Grounding (verified against the current code):**
- Catalog router: `app/api/catalog.py`, `APIRouter(prefix="/api/tenants/{tenant_id}", tags=["catalog"])`,
  helper `_load_device(session, tenant_id, device_id)`, guard `require_tenant(Action.DEVICE_VIEW)`.
- `catalog_provider.get_catalog(session, edition, version) -> dict | None` returns
  `{edition, version, generated_from, models, menu}`; each model has
  `{id, title, source, model_root, xml_path, endpoints, pages, fields, grids}`; `fields` is a **list** of
  `{path, type, confidence, [required], [default], [options]}`; `grids` is a list of
  `{path, endpoints, fields:[…]}`. Helpers: `_parse_version(v) -> tuple[int,...]`,
  `_community_versions(manifest) -> list[str]`.
- `config_model.build_tree(xml: str) -> dict` (with conservative secret redaction) + the snapshot
  decrypt path used by the existing `config/model` endpoint.
- Connector: `client.get_config_backup() -> str` (live config.xml).
- Frontend catalog components: `frontend/src/catalog/` — `CatalogEditorTab.tsx`, `CatalogMenuTree.tsx`,
  `CatalogModelForm.tsx`, `CatalogFieldInput.tsx`, `catalogHooks.ts`, `catalogTypes.ts`. Config tree:
  `frontend/src/config/ConfigTree.tsx`. UI strings live in `frontend/src/i18n/en.ts` under `catalog:`;
  **every key added to `en.ts` must be mirrored in all 11 other locale dicts or `tsc -b` fails**
  (`it es fr de pt nl ru ar zh zhTW ja`).

---

## Part A — Cross-version diff badges

### Task 1: `previous_version` + `published_versions` helpers (catalog_provider)

**Files:**
- Modify: `backend/app/services/catalog_provider.py`
- Test: `backend/tests/test_catalog_provider_versions.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_provider_versions.py
from app.services.catalog_provider import previous_version


def test_previous_version_picks_highest_strictly_below():
    versions = ["26.1", "26.1.1", "26.1.8", "26.1.9"]
    assert previous_version(versions, "26.1.9") == "26.1.8"
    assert previous_version(versions, "26.1.1") == "26.1"


def test_previous_version_none_when_lowest_or_unknown():
    versions = ["26.1", "26.1.9"]
    assert previous_version(versions, "26.1") is None
    assert previous_version(versions, "25.7") is None  # nothing strictly below
    assert previous_version([], "26.1.9") is None
```

- [ ] **Step 2: Run it — expect ImportError/fail**

Run: `cd backend && python -m pytest tests/test_catalog_provider_versions.py -q`
Expected: FAIL (`cannot import name 'previous_version'`).

- [ ] **Step 3: Implement the helpers**

Add to `backend/app/services/catalog_provider.py` (near `_parse_version` / `_community_versions`):

```python
def previous_version(versions: list[str], version: str) -> str | None:
    """Highest published version strictly less than `version` (semver-ish), or None."""
    target = _parse_version(version)
    below = [v for v in versions if _parse_version(v) < target]
    if not below:
        return None
    return max(below, key=_parse_version)


async def published_versions(edition: str = "community") -> list[str]:
    """All published versions for an edition (from the release manifest), sorted ascending.

    Network-only (no cache): used by the diff endpoint to list selectable baselines. Returns [] on any
    fetch error so the caller degrades to 'no baselines'."""
    settings = get_settings()
    base = settings.catalog_release_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as http:
            manifest = (await http.get(f"{base}/manifest.json")).raise_for_status().json()
    except (httpx.HTTPError, ValueError, KeyError):
        return []
    return sorted(_community_versions(manifest), key=_parse_version)
```

- [ ] **Step 4: Run it — expect pass**

Run: `cd backend && python -m pytest tests/test_catalog_provider_versions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catalog_provider.py backend/tests/test_catalog_provider_versions.py
git commit -m "feat(catalog): previous_version + published_versions helpers for cross-version diff"
```

### Task 2: `catalog_versions.diff_catalogs` (pure)

**Files:**
- Create: `backend/app/services/catalog_versions.py`
- Test: `backend/tests/test_catalog_versions.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_versions.py
from app.services.catalog_versions import diff_catalogs


def _cat(models):
    return {"models": models}


def test_diff_added_removed_changed_models_and_fields():
    a = _cat({
        "m.keep": {"fields": [{"path": "a", "type": "string"}, {"path": "b", "type": "string"}]},
        "m.gone": {"fields": [{"path": "x", "type": "string"}]},
    })
    b = _cat({
        "m.keep": {"fields": [
            {"path": "a", "type": "boolean"},          # changed (type)
            {"path": "c", "type": "string"},           # added
        ]},                                            # 'b' removed
        "m.new": {"fields": [{"path": "y", "type": "string"}]},  # added model
    })
    d = diff_catalogs(a, b)
    assert d["added_models"] == ["m.new"]
    assert d["removed_models"] == ["m.gone"]
    mk = d["models"]["m.keep"]
    assert mk["added_fields"] == ["c"]
    assert mk["removed_fields"] == ["b"]
    assert mk["changed_fields"] == ["a"]


def test_diff_identical_is_empty():
    a = _cat({"m": {"fields": [{"path": "a", "type": "string", "required": True}]}})
    d = diff_catalogs(a, a)
    assert d["added_models"] == [] and d["removed_models"] == []
    assert d["models"] == {}
```

- [ ] **Step 2: Run it — expect fail**

Run: `cd backend && python -m pytest tests/test_catalog_versions.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# backend/app/services/catalog_versions.py
"""Pure cross-version catalog diff for the editor's "new/changed since X" badges.

Reimplemented app-side (tools/ is offline-only) over the runtime catalog shape: models keyed by id,
each with a `fields` list of {path, type, [required], [default], [options]}."""
from __future__ import annotations

# Field attributes that constitute a "change" when they differ between versions.
_FIELD_ATTRS = ("type", "required", "default", "options")


def _fields_by_path(model: dict) -> dict[str, dict]:
    return {f["path"]: f for f in (model.get("fields") or []) if isinstance(f, dict) and "path" in f}


def _field_changed(a: dict, b: dict) -> bool:
    return any(a.get(k) != b.get(k) for k in _FIELD_ATTRS)


def diff_catalogs(a: dict, b: dict) -> dict:
    """Diff catalog `a` (baseline/from) vs `b` (device/to).

    Returns {added_models, removed_models, models: {mid: {added_fields, removed_fields, changed_fields}}}.
    Only models present in both with field-level differences appear under `models`."""
    ma, mb = a.get("models", {}) or {}, b.get("models", {}) or {}
    added_models = sorted(k for k in mb if k not in ma)
    removed_models = sorted(k for k in ma if k not in mb)
    models: dict[str, dict] = {}
    for mid in mb.keys() & ma.keys():
        fa, fb = _fields_by_path(ma[mid]), _fields_by_path(mb[mid])
        added = sorted(p for p in fb if p not in fa)
        removed = sorted(p for p in fa if p not in fb)
        changed = sorted(p for p in fb.keys() & fa.keys() if _field_changed(fa[p], fb[p]))
        if added or removed or changed:
            models[mid] = {"added_fields": added, "removed_fields": removed, "changed_fields": changed}
    return {"added_models": added_models, "removed_models": removed_models, "models": models}
```

- [ ] **Step 4: Run it — expect pass**

Run: `cd backend && python -m pytest tests/test_catalog_versions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/catalog_versions.py backend/tests/test_catalog_versions.py
git commit -m "feat(catalog): pure diff_catalogs (cross-version model/field diff)"
```

### Task 3: `GET /catalog/diff` endpoint

**Files:**
- Modify: `backend/app/api/catalog.py`
- Test: `backend/tests/test_catalog_diff_endpoint.py` (follow the existing catalog endpoint tests for the
  fixtures: a tenant, a device with `edition`/`firmware_version`, and `respx`/monkeypatch of
  `catalog_provider.get_catalog` + `published_versions`).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_catalog_diff_endpoint.py
import pytest
from app.services import catalog_provider

# Reuse the project's existing catalog-endpoint test harness/fixtures (client, a tenant, a device on
# 26.1.9). See tests/test_catalog_endpoints.py for the established pattern; mirror its fixtures here.

CAT_FROM = {"edition": "community", "version": "26.1.8",
            "models": {"m": {"fields": [{"path": "a", "type": "string"}]}}}
CAT_TO = {"edition": "community", "version": "26.1.9",
          "models": {"m": {"fields": [{"path": "a", "type": "boolean"}, {"path": "b", "type": "string"}]}}}


@pytest.mark.asyncio
async def test_diff_default_previous(client, device_on_2619, monkeypatch):
    async def fake_get_catalog(session, edition, version, **kw):
        return CAT_TO if version == "26.1.9" else CAT_FROM
    async def fake_versions(edition="community"):
        return ["26.1.8", "26.1.9"]
    monkeypatch.setattr(catalog_provider, "get_catalog", fake_get_catalog)
    monkeypatch.setattr(catalog_provider, "published_versions", fake_versions)

    r = await client.get(f"/api/tenants/{device_on_2619.tenant_id}/devices/{device_on_2619.id}/catalog/diff")
    assert r.status_code == 200
    body = r.json()
    assert body["from"] == "26.1.8" and body["to"] == "26.1.9"
    assert body["available_baselines"] == ["26.1.8"]
    assert body["diff"]["models"]["m"]["added_fields"] == ["b"]
    assert body["diff"]["models"]["m"]["changed_fields"] == ["a"]


@pytest.mark.asyncio
async def test_diff_no_baseline_is_empty(client, device_on_2619, monkeypatch):
    async def fake_get_catalog(session, edition, version, **kw):
        return CAT_TO
    async def fake_versions(edition="community"):
        return ["26.1.9"]  # device is the lowest → no previous
    monkeypatch.setattr(catalog_provider, "get_catalog", fake_get_catalog)
    monkeypatch.setattr(catalog_provider, "published_versions", fake_versions)
    r = await client.get(f"/api/tenants/{device_on_2619.tenant_id}/devices/{device_on_2619.id}/catalog/diff")
    assert r.status_code == 200
    body = r.json()
    assert body["from"] is None
    assert body["diff"] == {"added_models": [], "removed_models": [], "models": {}}


@pytest.mark.asyncio
async def test_diff_cross_tenant_404(client, other_tenant, device_on_2619):
    r = await client.get(f"/api/tenants/{other_tenant.id}/devices/{device_on_2619.id}/catalog/diff")
    assert r.status_code == 404
```

- [ ] **Step 2: Run it — expect fail**

Run: `cd backend && python -m pytest tests/test_catalog_diff_endpoint.py -q`
Expected: FAIL (404/route missing).

- [ ] **Step 3: Implement the endpoint**

Add to `backend/app/api/catalog.py` (imports `from app.services import catalog_versions`):

```python
@router.get("/devices/{device_id}/catalog/diff")
async def read_catalog_diff(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    from_version: str | None = Query(default=None, alias="from"),
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Cross-version catalog diff: the device's catalog vs a baseline (default the previous published)."""
    device = await _load_device(session, tenant_id, device_id)
    to_catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if to_catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    dev_ver = to_catalog.get("version", "")
    versions = await catalog_provider.published_versions(device.edition or "community")
    baselines = [v for v in versions if catalog_provider._parse_version(v)
                 < catalog_provider._parse_version(dev_ver)]
    chosen = from_version or catalog_provider.previous_version(versions, dev_ver)
    empty = {"added_models": [], "removed_models": [], "models": {}}
    if not chosen or chosen == dev_ver:
        return {"from": None, "to": dev_ver, "available_baselines": baselines, "diff": empty}
    from_catalog = await catalog_provider.get_catalog(session, device.edition, chosen)
    if from_catalog is None:
        return {"from": None, "to": dev_ver, "available_baselines": baselines, "diff": empty}
    return {
        "from": chosen, "to": dev_ver, "available_baselines": baselines,
        "diff": catalog_versions.diff_catalogs(from_catalog, to_catalog),
    }
```

(Ensure `Query` is imported from fastapi; `_load_device` raises 404 for a device not in the tenant.)

- [ ] **Step 4: Run it — expect pass**

Run: `cd backend && python -m pytest tests/test_catalog_diff_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/catalog.py backend/tests/test_catalog_diff_endpoint.py
git commit -m "feat(catalog): GET /catalog/diff endpoint (device vs baseline, DEVICE_VIEW)"
```

### Task 4: Frontend — baseline selector + diff badges

**Files:**
- Modify: `frontend/src/catalog/catalogTypes.ts`, `catalogHooks.ts`, `CatalogEditorTab.tsx`,
  `CatalogModelForm.tsx`, `CatalogMenuTree.tsx`
- Modify: `frontend/src/i18n/en.ts` + all 11 sibling dicts (`it es fr de pt nl ru ar zh zhTW ja`)
- Test: `frontend/src/catalog/__tests__/catalogDiff.test.tsx`

- [ ] **Step 1: Add i18n keys to `en.ts` (then mirror to all 11)**

In `frontend/src/i18n/en.ts`, extend the `catalog:` block:

```ts
    diff: {
      baseline: "Compare with",
      noBaseline: "No earlier version",
      newSince: "New since {v}",
      changedSince: "Changed since {v}",
      changes: "changes",
    },
```

Then add the SAME `diff` sub-block (translated values; keep `{v}` placeholder verbatim) to
`it.ts es.ts fr.ts de.ts pt.ts nl.ts ru.ts ar.ts zh.ts zhTW.ts ja.ts`. (Build fails on any omission.)

> Note: these strings use a `{v}` placeholder; the dictionaries are plain values, so the component does
> the substitution: `t.catalog.diff.newSince.replace("{v}", from)`.

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogDiff.test.tsx
import { render, screen } from "@testing-library/react";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it } from "vitest";
import { I18nProvider } from "../../i18n";
import { CatalogModelForm } from "../CatalogModelForm";

const model = { id: "m", title: "M", fields: [{ path: "a", type: "string" }], grids: [] };
const diff = { models: { m: { added_fields: ["a"], removed_fields: [], changed_fields: [] } } };

function wrap(ui: React.ReactElement) {
  return render(<I18nProvider><MantineProvider>{ui}</MantineProvider></I18nProvider>);
}

describe("diff badges", () => {
  it("renders 'New since' on an added field", () => {
    wrap(<CatalogModelForm model={model as any} values={{}} diff={diff as any} diffFrom="26.1.8" /* …other required props mocked… */ />);
    expect(screen.getByText(/New since 26\.1\.8/)).toBeInTheDocument();
  });
});
```

(Adapt the props to `CatalogModelForm`'s real signature — read it first; the point is: passing
`diff`/`diffFrom` renders a badge on fields listed in `added_fields`/`changed_fields`.)

- [ ] **Step 3: Run it — expect fail**

Run: `cd frontend && npm test -- catalogDiff`
Expected: FAIL.

- [ ] **Step 4: Implement**

- `catalogTypes.ts`: add `CatalogDiff` type (`{from: string|null; to: string; available_baselines: string[];
  diff: {added_models: string[]; removed_models: string[]; models: Record<string, {added_fields: string[];
  removed_fields: string[]; changed_fields: string[]}>}}`).
- `catalogHooks.ts`: `useCatalogDiff(deviceId, from)` — TanStack Query `GET
  /api/tenants/{tenant}/devices/{deviceId}/catalog/diff?from=…`, keyed by `[deviceId, from]`,
  `enabled: !!deviceId`.
- `CatalogEditorTab.tsx`: add a Mantine `Select` (label `t.catalog.diff.baseline`, data from
  `available_baselines`, default = `diff.from`) in the left pane header; thread the active `diff` +
  `from` down to `CatalogMenuTree` and `CatalogModelForm`.
- `CatalogModelForm.tsx`: for each field, if `diff?.models[model.id]?.added_fields.includes(field.path)`
  → render a small `<Badge color="teal">{t.catalog.diff.newSince.replace("{v}", from)}</Badge>`; else if
  in `changed_fields` → `<Badge color="yellow">{…changedSince…}</Badge>`.
- `CatalogMenuTree.tsx`: if a model id is in `diff.added_models` or has a non-empty `diff.models[id]`,
  show a small dot/count badge next to the node.
- No baseline (`from` null) → pass no diff → no badges.

- [ ] **Step 5: Run tests + build gate**

Run: `cd frontend && npm test -- catalogDiff && npm run build`
Expected: PASS + build green (proves all 12 dicts have the `diff` keys).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/catalog frontend/src/i18n
git commit -m "feat(catalog/ui): baseline selector + cross-version diff badges in the editor"
```

---

## Part B — Live config.xml map (cross-referenced)

### Task 5: `config_map.annotate_with_catalog` (pure)

**Files:**
- Create: `backend/app/services/config_map.py`
- Test: `backend/tests/test_config_map.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_map.py
from app.services.config_map import annotate_with_catalog

# build_tree-shaped node: {tag, path, attributes, children, [value]}
TREE = {
    "tag": "opnsense", "path": "opnsense", "attributes": {}, "children": [
        {"tag": "unboundplus", "path": "opnsense/unboundplus", "attributes": {}, "children": [
            {"tag": "general", "path": "opnsense/unboundplus/general", "attributes": {}, "children": []},
        ]},
        {"tag": "legacything", "path": "opnsense/legacything", "attributes": {}, "children": []},
    ],
}
CATALOG = {"models": {"unbound.x": {"xml_path": "OPNsense/unboundplus"}}}


def test_nodes_under_a_model_xml_path_are_editable():
    out = annotate_with_catalog(TREE, CATALOG)
    unbound = out["children"][0]
    assert unbound["editable"] is True and unbound["catalog_model_id"] == "unbound.x"
    assert out["children"][0]["children"][0]["editable"] is True  # subtree inherits
    assert out["children"][1]["editable"] is False  # legacything → read-only
    assert "catalog_model_id" not in out["children"][1]


def test_index_suffixed_paths_match_on_tag_prefix():
    tree = {"tag": "opnsense", "path": "opnsense", "attributes": {}, "children": [
        {"tag": "unboundplus", "path": "opnsense/unboundplus[1]", "attributes": {}, "children": []},
    ]}
    out = annotate_with_catalog(tree, {"models": {"u": {"xml_path": "OPNsense/unboundplus"}}})
    assert out["children"][0]["editable"] is True
```

- [ ] **Step 2: Run it — expect fail**

Run: `cd backend && python -m pytest tests/test_config_map.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement**

```python
# backend/app/services/config_map.py
"""Annotate a build_tree config.xml tree with catalog coverage (read-only cross-reference).

Each node is tagged editable/read-only: a node whose config.xml path falls under a catalog model's
`xml_path` mount is editable-via-that-model; everything else is read-only (legacy / non-MVC, no API)."""
from __future__ import annotations

import re

_INDEX = re.compile(r"\[\d+\]$")


def _norm(path: str) -> str:
    """Lowercase, strip [n] index suffixes from each segment — for prefix matching."""
    return "/".join(_INDEX.sub("", seg) for seg in path.lower().split("/"))


def _model_mounts(catalog: dict) -> list[tuple[str, str]]:
    """(normalised xml_path, model_id), longest-path first so the most specific model wins."""
    out = []
    for mid, m in (catalog.get("models", {}) or {}).items():
        xp = m.get("xml_path")
        if xp:
            out.append((_norm(xp), mid))
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return out


def _covering_model(norm_path: str, mounts: list[tuple[str, str]]) -> str | None:
    for mount, mid in mounts:
        if norm_path == mount or norm_path.startswith(mount + "/"):
            return mid
    return None


def annotate_with_catalog(tree: dict, catalog: dict) -> dict:
    """Return a deep copy of `tree` with `editable: bool` and (when editable) `catalog_model_id` set."""
    mounts = _model_mounts(catalog)

    def walk(node: dict) -> dict:
        mid = _covering_model(_norm(node.get("path", "")), mounts)
        new = {**node}
        new["editable"] = mid is not None
        if mid is not None:
            new["catalog_model_id"] = mid
        new["children"] = [walk(c) for c in node.get("children", [])]
        return new

    return walk(tree)
```

> Note: `build_tree` paths start at the config root (e.g. `opnsense/unboundplus`) while catalog
> `xml_path` is mounted as `OPNsense/unboundplus`. `_norm` lowercases both so they align. Verify the real
> `build_tree` root tag casing while implementing and adjust the test fixtures to match (the matching is
> case-insensitive by design).

- [ ] **Step 4: Run it — expect pass**

Run: `cd backend && python -m pytest tests/test_config_map.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/config_map.py backend/tests/test_config_map.py
git commit -m "feat(config): pure annotate_with_catalog (config.xml ↔ catalog cross-reference)"
```

### Task 6: `GET /config/map` endpoint (live + snapshot fallback)

**Files:**
- Modify: `backend/app/api/catalog.py` (or the device/config router if the existing `config/model`
  endpoint lives elsewhere — place `config/map` beside it for consistency; read where `config/model` is
  defined first and follow it).
- Test: `backend/tests/test_config_map_endpoint.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_config_map_endpoint.py — mirror the existing config/model endpoint test harness.
# live: monkeypatch the connector's get_config_backup to return a small config.xml; assert source=live,
#   reachable=true, and an annotated node carries editable/catalog_model_id.
# unreachable: make the connector raise → assert source=snapshot, reachable=false, taken_at present,
#   tree still annotated (from the decrypted snapshot).
# no snapshot + unreachable → 404. cross-tenant → 404. (Redaction: assert a <password> node's value is
#   redacted in the returned tree, reusing build_tree's behaviour.)
```

(Write the concrete cases following `tests/test_config_endpoints.py` / the existing `config/model`
test — same fixtures for a device, a stored snapshot, and the connector mock.)

- [ ] **Step 2: Run it — expect fail**

Run: `cd backend && python -m pytest tests/test_config_map_endpoint.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement the endpoint**

Reusing the same building blocks as `config/model` (build_tree, the snapshot decrypt path) and the
connector factory used by `drift-check`:

```python
@router.get("/devices/{device_id}/config/map")
async def read_config_map(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Read-only live config.xml tree, cross-referenced to the catalog. Falls back to the latest snapshot
    (labelled stale) if the device is unreachable; 404 if neither is available."""
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    catalog = catalog or {"models": {}}

    # 1) try live
    try:
        client = build_client(device)                 # same factory drift-check uses
        xml = await client.get_config_backup()
        tree = config_model.build_tree(xml)            # redaction applied here
        return {"source": "live", "reachable": True,
                "tree": config_map.annotate_with_catalog(tree, catalog)}
    except Exception:                                  # connector/credential/parse error → degrade
        pass

    # 2) fall back to the latest stored snapshot
    snap = await _latest_snapshot(session, device_id)  # same accessor config/model uses
    if snap is None:
        raise HTTPException(status_code=404, detail="No config available for this device")
    tree = config_model.build_tree(_decrypt_snapshot_xml(snap))
    return {"source": "snapshot", "reachable": False, "taken_at": snap.created_at,
            "tree": config_map.annotate_with_catalog(tree, catalog)}
```

> While implementing, replace `build_client`, `_latest_snapshot`, `_decrypt_snapshot_xml` with the EXACT
> helpers the existing `config/model` + `drift-check` endpoints use (read them first; do not invent new
> decrypt/connector code — reuse the audited paths so redaction + SSRF guards are preserved).

- [ ] **Step 4: Run it — expect pass**

Run: `cd backend && python -m pytest tests/test_config_map_endpoint.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/catalog.py backend/tests/test_config_map_endpoint.py
git commit -m "feat(config): GET /config/map (live config.xml + catalog cross-ref, snapshot fallback)"
```

### Task 7: Frontend — Config map view in the editor

**Files:**
- Modify: `frontend/src/catalog/CatalogEditorTab.tsx`, `catalogHooks.ts`, `catalogTypes.ts`
- Reuse/extend: `frontend/src/config/ConfigTree.tsx` (or a thin wrapper `ConfigMapTree.tsx`)
- Modify: `frontend/src/i18n/en.ts` + all 11 sibling dicts
- Test: `frontend/src/catalog/__tests__/configMap.test.tsx`

- [ ] **Step 1: Add i18n keys to `en.ts` (then mirror to all 11)**

Extend the `catalog:` block:

```ts
    map: {
      tabMenu: "Menu",
      tabMap: "Config map",
      editInCatalog: "Edit in catalog",
      readOnly: "read-only (no API)",
      staleBanner: "Stale — device unreachable; showing the last backup from {when}",
    },
```

Mirror to all 11 locale dicts (keep `{when}` verbatim).

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/configMap.test.tsx
// Render the config-map view with a small annotated tree (one editable node w/ catalog_model_id, one
// read-only). Assert: editable node shows an "Edit in catalog" control; read-only node shows the
// "read-only (no API)" marker; a source:"snapshot" response renders the stale banner; clicking
// "Edit in catalog" calls the onSelectModel callback with the model id.
```

- [ ] **Step 3: Run it — expect fail**

Run: `cd frontend && npm test -- configMap`
Expected: FAIL.

- [ ] **Step 4: Implement**

- `catalogTypes.ts`: `ConfigMapResponse` type (`{source: "live"|"snapshot"; reachable: boolean;
  taken_at?: string; tree: MapNode}`, `MapNode = {tag; path; attributes; value?; editable: boolean;
  catalog_model_id?: string; children: MapNode[]}`).
- `catalogHooks.ts`: `useConfigMap(deviceId)` — `GET …/config/map`, `enabled: !!deviceId`.
- `CatalogEditorTab.tsx`: add a SegmentedControl/toggle in the left pane between `t.catalog.map.tabMenu`
  and `t.catalog.map.tabMap`. In "Config map" mode render the annotated tree:
  - editable node → an **"Edit in catalog"** affordance that switches back to Menu mode and selects
    `catalog_model_id` (reuse the existing model-selection state/handler);
  - `editable: false` node → a muted `t.catalog.map.readOnly` marker;
  - `source === "snapshot"` → a Mantine `Alert` with `t.catalog.map.staleBanner.replace("{when}", taken_at)`.
- The existing snapshot `config/model` view in the **Config tab is unchanged**.

- [ ] **Step 5: Run tests + build gate**

Run: `cd frontend && npm test -- configMap && npm run build`
Expected: PASS + build green (all 12 dicts have the `map` keys).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/catalog frontend/src/config frontend/src/i18n
git commit -m "feat(catalog/ui): Config map view (live config.xml ↔ catalog, edit-in-catalog, stale banner)"
```

---

## Task 8: Full gate + finish

- [ ] **Step 1: Backend suite + lint**

Run: `cd backend && python -m pytest -q && ruff check app/`
Expected: all green.

- [ ] **Step 2: Frontend gate**

Run: `cd frontend && npm test && npm run build && npm run lint`
Expected: all green (build proves i18n parity across all 12 locales).

- [ ] **Step 3: Update the README Project-status footnote ⁴**

In `README.md`, the catalog-editor row footnote ⁴ lists "Remaining: 3c …". Mark **3c done** (diff badges
+ live config.xml map shipped); leave sub-project 4 (Business proprietary deltas) as the only remainder.

- [ ] **Step 4: Push + PR + green CI + squash-merge** (protected main).

---

## Self-review notes

- **Spec coverage:** Part A (diff backend Tasks 1–2, endpoint Task 3, UI Task 4) + Part B (map backend
  Task 5, endpoint Task 6, UI Task 7) — both parts' backend/endpoint/frontend/tests are covered, plus the
  out-of-scope items are respected (model-level map granularity; on-demand diff; Config tab unchanged).
- **i18n parity:** Tasks 4 & 7 explicitly add keys to all 12 dicts and gate on `npm run build` — the
  project's hard constraint.
- **Security:** both endpoints `DEVICE_VIEW` + `_load_device` ownership; map reuses `build_tree` redaction
  and the existing snapshot-decrypt/connector paths (no new secret/SSRF surface); read-only throughout.
- **Type consistency:** `diff_catalogs` output shape is identical across the backend test, the endpoint,
  `catalogTypes.CatalogDiff`, and the badge logic; `annotate_with_catalog` adds `editable`/`catalog_model_id`
  consumed verbatim by `ConfigMapResponse`/`MapNode`.
