# Catalog Editor Foundation (sub-project 3a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On a device, open any catalog model, see its live current values in a generated form, edit scalars + grids, and propose a change through the existing pipeline — the editing foundation of the OPNsense-like console.

**Architecture:** A new backend read endpoint merges the device's catalog model (from the provider, #94) with **live** values read from the device (`<model>/settings/get`), flattened by pure functions that reuse the existing option-object normalization. A new frontend `catalog/` module adds an "Editor" tab to the device page: a searchable model list + a generated form (field-type → Mantine input) + grid tables, whose "Propose" creates a draft via the existing `POST …/catalog/changes` (#94). The existing schedule/snapshot/revert pipeline + `ChangesPanel` handle everything after the draft.

**Tech Stack:** Backend — Python 3.14, FastAPI, SQLAlchemy, httpx, respx (test). Frontend — React 19, Mantine v9, @tanstack/react-query, openapi-fetch (typed via generated `schema.d.ts`), vitest + Testing Library + MSW.

**Spec:** `docs/superpowers/specs/2026-06-13-catalog-editor-foundation-design.md`

**Branch:** `feat/catalog-editor-foundation` (already checked out; the spec commit is already on it).

---

## Conventions

- Backend: run from `backend/` with the venv + DB env. A test DB must be reachable:
  ```bash
  export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
  export ADMIN_DATABASE_URL="$TEST_DATABASE_URL"
  cd backend && .venv/bin/pytest tests/<file>.py -q
  ```
  Lint: `cd backend && .venv/bin/ruff check app/`.
- Frontend: run from `frontend/` with `npm`:
  ```bash
  cd frontend && npm run test -- <path>     # vitest (single file: append the path)
  cd frontend && npm run build              # tsc -b + vite build (the CI build gate)
  cd frontend && npm run lint               # eslint
  ```
- Commit after each task. English in code/commits; chat stays Italian (controller concern).
- **Data shapes used across tasks** (fixed — do not deviate):
  - Backend model endpoint response:
    ```json
    {
      "model": { "id": "unbound", "title": "Unbound", "model_root": "unbound",
                 "endpoints": {"get": "unbound/settings/get", "set": "...", "reconfigure": "..."},
                 "fields": [{"path": "general.enabled", "type": "bool"}],
                 "grids": [{"path": "hosts", "endpoints": {...}, "fields": [{"path": "hostname", "type": "string"}]}],
                 "pages": [{"id": "general", "fields": ["general.enabled"]}] },
      "values": { "general.enabled": "1", "general.port": "53" },
      "grids":  { "hosts": [ { "uuid": "ab-12", "hostname": "web", "server": "10.0.0.10" } ] },
      "reachable": true,
      "read_only": false
    }
    ```
  - A scalar value is a **string** except a `multienum`, which is a **list of selected keys**.
  - `CatalogChangeIn` (already in the backend from #94): `{ model_id, scalars: {path: string}, grids: [{op, grid, uuid?, item?}] }`. `scalars` values are strings (multienum → comma-joined keys).

---

## Phase A — Backend: live model values

### Task A1: Shared option-value helpers (`opnsense_values.py`)

Extract the option-object normalization from `setting_introspect.py` into a shared module so the new
`catalog_live` flattener reuses it without duplicating logic. Behaviour is unchanged.

**Files:**
- Create: `app/services/opnsense_values.py`
- Modify: `app/services/setting_introspect.py`
- Test: `tests/test_opnsense_values.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opnsense_values.py
from app.services.opnsense_values import is_option_dict, options, selected


def test_is_option_dict_true_for_selected_objects():
    v = {"a": {"value": "A", "selected": "1"}, "b": {"value": "B", "selected": "0"}}
    assert is_option_dict(v) is True


def test_is_option_dict_false_for_plain_or_nested():
    assert is_option_dict({"x": "1"}) is False
    assert is_option_dict({}) is False


def test_options_maps_key_to_label():
    v = {"a": {"value": "Label A", "selected": "0"}}
    assert options(v) == [{"value": "a", "label": "Label A"}]


def test_selected_returns_keys_with_selected_1():
    v = {"a": {"value": "A", "selected": "1"}, "b": {"value": "B", "selected": "0"}}
    assert selected(v) == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_opnsense_values.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.opnsense_values'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/opnsense_values.py
"""Shared normalization of OPNsense model `get` responses.

OPNsense renders option/enum fields as a dict of {key: {"value": <label>, "selected": "0"|"1"}}.
These helpers turn that into options + the selected key(s). Used by the introspection form builder
and the catalog editor's live-value flattener.
"""


def is_option_dict(v) -> bool:
    return isinstance(v, dict) and len(v) > 0 and all(
        isinstance(o, dict) and "selected" in o for o in v.values())


def options(v: dict) -> list[dict]:
    return [{"value": k, "label": str(o.get("value", k))} for k, o in v.items()]


def selected(v: dict) -> list[str]:
    return [k for k, o in v.items() if str(o.get("selected")) == "1"]
```

Then change `setting_introspect.py` to import + delegate (remove its private copies). Replace the
`_is_option_dict`, `_options`, `_selected` definitions and their call sites:

```python
# app/services/setting_introspect.py — top of file
from app.services.opnsense_values import is_option_dict, options, selected
```
Delete the local `def _is_option_dict`, `def _options`, `def _selected`, and in `_walk` replace
`_is_option_dict(val)` → `is_option_dict(val)`, `_options(val)` → `options(val)`,
`_selected(val)` → `selected(val)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && .venv/bin/pytest tests/test_opnsense_values.py tests/test_setting_introspect.py -q`
Expected: PASS (the existing introspection tests still pass — behaviour unchanged).

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/opnsense_values.py app/services/setting_introspect.py tests/test_opnsense_values.py
git commit -m "refactor(catalog): extract shared OPNsense option-value helpers"
```

---

### Task A2: `catalog_live.flatten_values` (pure)

**Files:**
- Create: `app/services/catalog_live.py`
- Test: `tests/test_catalog_live.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_live.py
from app.services.catalog_live import flatten_values

_MODEL = {
    "model_root": "unbound",
    "fields": [
        {"path": "general.enabled", "type": "bool"},
        {"path": "general.port", "type": "int"},
        {"path": "general.dnssec", "type": "multienum"},
    ],
}


def test_flatten_scalars_and_option_dicts():
    get_response = {"unbound": {"general": {
        "enabled": "1",
        "port": "53",
        "dnssec": {"a": {"value": "A", "selected": "1"}, "b": {"value": "B", "selected": "0"}},
    }}}
    out = flatten_values(get_response, _MODEL)
    assert out["general.enabled"] == "1"
    assert out["general.port"] == "53"
    assert out["general.dnssec"] == ["a"]  # multi-select -> selected keys


def test_flatten_missing_model_root_is_empty():
    assert flatten_values({}, _MODEL) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.catalog_live'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/services/catalog_live.py
"""Merge a catalog model's schema with the device's LIVE values (from its `<model>/settings/get`).

Pure + device-independent: the API layer fetches the raw `get` response and hands it here. Option/enum
dicts are normalized to their selected key(s); grid (uuid-keyed) nodes are returned as row lists.
"""
from app.services.opnsense_values import is_option_dict, selected


def _scalar(value) -> str | list[str] | None:
    """A leaf's current value: option-dict -> selected key(s); plain string -> itself; else None."""
    if is_option_dict(value):
        return selected(value)
    if isinstance(value, str):
        return value
    return None


def flatten_values(get_response: dict, model: dict) -> dict[str, str | list[str]]:
    """{dotted_path: current_value} for the model's scalar leaves (grids handled separately)."""
    root = (get_response or {}).get(model.get("model_root", ""), {})
    out: dict[str, str | list[str]] = {}

    def walk(node, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        for key, val in node.items():
            path = f"{prefix}.{key}" if prefix else key
            leaf = _scalar(val)
            if leaf is not None:
                out[path] = leaf
            elif isinstance(val, dict):
                walk(val, path)  # nested object -> recurse (grids are filtered out below)

    walk(root, "")
    # Keep only paths the catalog declares as scalar fields (drops grid nodes + unknown extras).
    field_paths = {f["path"] for f in model.get("fields", [])}
    return {p: v for p, v in out.items() if p in field_paths}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_live.py tests/test_catalog_live.py
git commit -m "feat(catalog): catalog_live.flatten_values — live scalar values per catalog field"
```

---

### Task A3: `catalog_live.extract_grid_rows` (pure)

**Files:**
- Modify: `app/services/catalog_live.py`
- Test: `tests/test_catalog_live.py` (add)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/test_catalog_live.py
from app.services.catalog_live import extract_grid_rows

_GRID = {"path": "hosts", "fields": [{"path": "hostname", "type": "string"},
                                     {"path": "rr", "type": "enum"}]}


def test_extract_grid_rows_uuid_keyed_with_option_cell():
    get_response = {"unbound": {"hosts": {
        "ab-12": {"hostname": "web", "rr": {"A": {"value": "A", "selected": "1"}}},
        "cd-34": {"hostname": "db", "rr": {"A": {"value": "A", "selected": "0"}}},
    }}}
    rows = extract_grid_rows(get_response, _MODEL, _GRID)
    assert {"uuid": "ab-12", "hostname": "web", "rr": ["A"]} in rows
    assert {"uuid": "cd-34", "hostname": "db", "rr": []} in rows


def test_extract_grid_rows_missing_node_is_empty():
    assert extract_grid_rows({}, _MODEL, _GRID) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -k grid -q`
Expected: FAIL — `ImportError: cannot import name 'extract_grid_rows'`

- [ ] **Step 3: Write minimal implementation (append to catalog_live.py)**

```python
# append to app/services/catalog_live.py
def extract_grid_rows(get_response: dict, model: dict, grid: dict) -> list[dict]:
    """Rows of one ArrayField grid: the device returns a uuid-keyed dict {uuid: {field: value}}.

    Returns [{"uuid": <uuid>, <field>: <normalized value>, ...}] for the grid's catalog fields.
    """
    root = (get_response or {}).get(model.get("model_root", ""), {})
    node = root
    for part in grid["path"].split("."):
        node = node.get(part, {}) if isinstance(node, dict) else {}
    if not isinstance(node, dict):
        return []
    field_paths = [f["path"] for f in grid.get("fields", [])]
    rows: list[dict] = []
    for uuid, cells in node.items():
        if not isinstance(cells, dict):
            continue
        row: dict = {"uuid": uuid}
        for fp in field_paths:
            leaf = _scalar(cells.get(fp))
            row[fp] = leaf if leaf is not None else ""
        rows.append(row)
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_live.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/services/catalog_live.py tests/test_catalog_live.py
git commit -m "feat(catalog): catalog_live.extract_grid_rows — live grid rows per catalog grid"
```

---

### Task A4: Live model endpoint `GET …/catalog/models/{model_id}`

**Files:**
- Modify: `app/api/catalog.py`
- Test: `tests/test_catalog_api.py` (add)

- [ ] **Step 1: Write the failing test (append to tests/test_catalog_api.py)**

The file already has `_CATALOG`, `_fake_get_catalog`, `_seed`, `_device`, `_login` helpers. Add:

```python
# append to tests/test_catalog_api.py
import respx
from httpx import Response


def _live_get_payload():
    # what the device returns for unbound/settings/get
    return {"unbound": {
        "general": {"enabled": "1", "port": "53"},
        "hosts": {"ab-12": {"hostname": "web", "server": "10.0.0.10"}},
    }}


@respx.mock
async def test_read_model_merges_live_values(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    respx.post("https://x/api/unbound/settings/get").mock(  # connector get_setting is a GET? see note
        return_value=Response(200, json=_live_get_payload()))
    respx.get("https://x/api/unbound/settings/get").mock(
        return_value=Response(200, json=_live_get_payload()))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/unbound",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is True and body["read_only"] is False
    assert body["values"]["general.enabled"] == "1"
    assert body["grids"]["hosts"][0]["uuid"] == "ab-12"
    assert body["model"]["id"] == "unbound"


@respx.mock
async def test_read_model_unreachable_degrades(api_client, db_engine, monkeypatch):
    import httpx
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    respx.get("https://x/api/unbound/settings/get").mock(side_effect=httpx.ConnectError("down"))
    respx.post("https://x/api/unbound/settings/get").mock(side_effect=httpx.ConnectError("down"))
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/unbound",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False and body["values"] == {}


async def test_read_model_unknown_404(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/nope",
        headers=csrf_headers(api_client))
    assert r.status_code == 404


async def test_read_model_denylist_is_read_only_no_live(api_client, db_engine, monkeypatch):
    monkeypatch.setattr(catalog_provider, "get_catalog", _fake_get_catalog)
    tid = await _seed(db_engine)
    did = await _device(db_engine, tid)
    await _login(api_client)
    r = await api_client.get(
        f"/api/tenants/{tid}/devices/{did}/catalog/models/interfaces",
        headers=csrf_headers(api_client))
    assert r.status_code == 200
    body = r.json()
    assert body["read_only"] is True
    assert body["values"] == {} and body["reachable"] is False
```

> **Note on the connector verb:** `OpnsenseClient.get_setting(get_path)` issues an HTTP **GET** to
> `/api/<get_path>`. The respx `get(...)` mock is the one that matters; the `post(...)` mock is belt-
> and-suspenders and can be dropped once you confirm the verb. The device base_url in `_device` is
> `https://x`, so the mocked URL is `https://x/api/unbound/settings/get`.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_api.py -k read_model -q` (DB env set)
Expected: FAIL — 404/405 (route not registered)

- [ ] **Step 3: Write minimal implementation**

Add to `app/api/catalog.py` (it already imports `catalog_provider`, `CATALOG_DENYLIST`, `_load_device`,
`OpnsenseClient`? — add the missing imports). At the top, add:

```python
from cryptography.fernet import InvalidToken

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.services.catalog_live import extract_grid_rows, flatten_values
```

Add the route (after `read_device_catalog`):

```python
@router.get("/devices/{device_id}/catalog/models/{model_id}")
async def read_catalog_model(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    model_id: str,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """A catalog model's schema + the device's LIVE current values (for the editor form).

    Reads `<model>/settings/get` live; degrades to reachable:false on any connector/credential error.
    Denylisted models are returned read_only with no live read."""
    device = await _load_device(session, tenant_id, device_id)
    catalog = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    if catalog is None:
        raise HTTPException(status_code=404, detail="No catalog available for this device version")
    model = catalog.get("models", {}).get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id!r}")
    base = {"model": model, "values": {}, "grids": {}, "reachable": False,
            "read_only": model_id in CATALOG_DENYLIST}
    if base["read_only"]:
        return base
    try:
        client = OpnsenseClient(
            device.base_url, crypto.decrypt(device.api_key_enc), crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls, tls_fingerprint=device.tls_fingerprint,
            edition=device.edition, version=device.firmware_version or "")
        raw = await client.get_setting(model["endpoints"]["get"])
    except (OpnsenseError, InvalidToken, KeyError):
        return base  # unreachable / unreadable -> schema only, editing disabled
    base["reachable"] = True
    base["values"] = flatten_values(raw, model)
    base["grids"] = {g["path"]: extract_grid_rows(raw, model, g) for g in model.get("grids", [])}
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && .venv/bin/pytest tests/test_catalog_api.py -q` (DB env set)
Expected: PASS (the 4 new tests + the existing 7). Then `cd backend && .venv/bin/ruff check app/`.

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/api/catalog.py tests/test_catalog_api.py
git commit -m "feat(catalog): live model endpoint — catalog schema + device current values"
```

---

## Phase B — Frontend: the "Editor" tab

### Task B1: Regenerate the typed API client

The frontend's typed client is generated from the backend OpenAPI. Regenerate it so the catalog
endpoints (`GET …/catalog`, `GET …/catalog/models/{id}`, `POST …/catalog/changes`) and `CatalogChangeIn`
are available to TypeScript.

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (generated), `frontend/openapi.json` (generated, if tracked)

- [ ] **Step 1: Regenerate**

Run (the backend venv + env must be set so `export_openapi.py` can import the app):
```bash
cd frontend && npm run gen:api
```
Expected: `src/api/schema.d.ts` updated; `git diff --stat` shows new catalog paths/components.

- [ ] **Step 2: Verify the catalog paths/types are present**

Run: `grep -c "catalog/models" frontend/src/api/schema.d.ts && grep -c "CatalogChangeIn" frontend/src/api/schema.d.ts`
Expected: both ≥ 1.

- [ ] **Step 3: Verify the project still type-checks**

Run: `cd frontend && npm run build`
Expected: PASS (tsc -b + vite build).

- [ ] **Step 4: Commit**

```bash
cd frontend && git add src/api/schema.d.ts openapi.json
git commit -m "chore(api): regenerate typed client for the catalog endpoints"
```

> If `openapi.json` is gitignored, omit it from the `git add`.

---

### Task B2: i18n strings for the editor

**Files:**
- Modify: `frontend/src/i18n/en.ts`

- [ ] **Step 1: Add a `catalog` block**

Add this block to the `en` object (anywhere among the feature blocks; keep alphabetical-ish):

```ts
  catalog: {
    tab: "Editor",
    searchModels: "Search settings…",
    noModels: "No catalog for this device version.",
    selectModel: "Select a setting group on the left to edit it.",
    readOnly: "Not editable (safety denylist).",
    unreachable: "Device unreachable — live values are required to edit.",
    liveValues: "Live values",
    propose: "Propose change",
    preview: "Review change",
    noChanges: "No changes to propose.",
    proposed: "Draft change created — schedule it from the Config tab.",
    proposeFailed: "Could not create the change.",
    loadFailed: "Could not load the model.",
    grid: { add: "Add row", edit: "Edit", delete: "Delete", empty: "No rows." },
  },
```

- [ ] **Step 2: Verify type-check**

Run: `cd frontend && npx tsc -b`
Expected: PASS (the `Dict` type widens to include `catalog`).

- [ ] **Step 3: Commit**

```bash
cd frontend && git add src/i18n/en.ts
git commit -m "i18n(catalog): editor strings"
```

---

### Task B3: Catalog hooks + types

**Files:**
- Create: `frontend/src/catalog/catalogTypes.ts`
- Create: `frontend/src/catalog/catalogHooks.ts`
- Test: `frontend/src/catalog/__tests__/catalogHooks.test.tsx`

- [ ] **Step 1: Write the types**

```ts
// frontend/src/catalog/catalogTypes.ts
export type CatalogField = {
  path: string;
  type: "bool" | "int" | "string" | "enum" | "multienum" | "network" | "ref" | "raw";
  options?: string[];
  label?: string;
  required?: boolean;
};

export type CatalogGrid = {
  path: string;
  endpoints: Record<string, string>;
  fields: CatalogField[];
};

export type CatalogModel = {
  id: string;
  title: string;
  model_root: string;
  endpoints: Record<string, string>;
  fields: CatalogField[];
  grids: CatalogGrid[];
  pages: { id: string; fields: string[] }[];
  read_only?: boolean;
};

export type GridRow = { uuid: string } & Record<string, string | string[]>;

export type CatalogModelLive = {
  model: CatalogModel;
  values: Record<string, string | string[]>;
  grids: Record<string, GridRow[]>;
  reachable: boolean;
  read_only: boolean;
};

export type CatalogGridOp =
  | { op: "add"; grid: string; item: Record<string, string> }
  | { op: "set"; grid: string; uuid: string; item: Record<string, string> }
  | { op: "del"; grid: string; uuid: string };

export type CatalogChangeBody = {
  model_id: string;
  scalars: Record<string, string>;
  grids: CatalogGridOp[];
};
```

- [ ] **Step 2: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogHooks.test.tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { I18nProvider } from "../../i18n";
import { useCatalogModel } from "../catalogHooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <TenantContext.Provider
        value={{ tenants: [], activeId: "t1", setActiveId: () => {}, loading: false }}>
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </TenantContext.Provider>
    </I18nProvider>
  );
}

describe("useCatalogModel", () => {
  it("loads a model with live values", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound", () =>
        HttpResponse.json({
          model: { id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
                   fields: [{ path: "general.enabled", type: "bool" }], grids: [], pages: [] },
          values: { "general.enabled": "1" }, grids: {}, reachable: true, read_only: false,
        })),
    );
    const { result } = renderHook(() => useCatalogModel("d1", "unbound"), { wrapper });
    await waitFor(() => expect(result.current.data?.reachable).toBe(true));
    expect(result.current.data?.values["general.enabled"]).toBe("1");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogHooks.test.tsx`
Expected: FAIL — cannot find `../catalogHooks`.

- [ ] **Step 4: Write the hooks**

```tsx
// frontend/src/catalog/catalogHooks.ts
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { CatalogChangeBody, CatalogModel, CatalogModelLive } from "./catalogTypes";

export function useDeviceCatalog(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useQuery({
    queryKey: ["device-catalog", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<{ resolved_version: string; models: Record<string, CatalogModel> }> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/catalog",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.catalog.loadFailed);
      return data as { resolved_version: string; models: Record<string, CatalogModel> };
    },
  });
}

export function useCatalogModel(deviceId: string, modelId: string | null) {
  const { activeId } = useTenant();
  const t = useT();
  return useQuery({
    queryKey: ["catalog-model", activeId, deviceId, modelId],
    enabled: !!activeId && !!deviceId && !!modelId,
    queryFn: async (): Promise<CatalogModelLive> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/catalog/models/{model_id}",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, model_id: modelId! } } },
      );
      if (error || !data) throw new Error(t.catalog.loadFailed);
      return data as CatalogModelLive;
    },
  });
}

export function useProposeCatalogChange(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (body: CatalogChangeBody) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/catalog/changes",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body },
      );
      if (error || !data) throw new Error(t.catalog.proposeFailed);
      return data;
    },
  });
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogHooks.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd frontend && git add src/catalog/catalogTypes.ts src/catalog/catalogHooks.ts src/catalog/__tests__/catalogHooks.test.tsx
git commit -m "feat(catalog): editor hooks + types (device catalog, live model, propose)"
```

---

### Task B4: `CatalogFieldInput` (field type → control)

**Files:**
- Create: `frontend/src/catalog/CatalogFieldInput.tsx`
- Test: `frontend/src/catalog/__tests__/catalogFieldInput.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogFieldInput.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogFieldInput } from "../CatalogFieldInput";

describe("CatalogFieldInput", () => {
  it("renders a switch for bool and reports 1/0", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <CatalogFieldInput field={{ path: "general.enabled", type: "bool" }} value="0" onChange={onChange} disabled={false} />,
    );
    fireEvent.click(screen.getByTestId("catalog-field-general.enabled"));
    expect(onChange).toHaveBeenCalledWith("general.enabled", "1");
  });

  it("renders a number input for int", () => {
    renderWithProviders(
      <CatalogFieldInput field={{ path: "general.port", type: "int" }} value="53" onChange={() => {}} disabled={false} />,
    );
    expect(screen.getByTestId("catalog-field-general.port")).toHaveValue("53");
  });

  it("renders a select for enum with options", () => {
    renderWithProviders(
      <CatalogFieldInput
        field={{ path: "x", type: "enum", options: ["a", "b"] }}
        value="a" onChange={() => {}} disabled={false} />,
    );
    expect(screen.getByTestId("catalog-field-x")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogFieldInput.test.tsx`
Expected: FAIL — cannot find `../CatalogFieldInput`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/catalog/CatalogFieldInput.tsx
import { MultiSelect, NumberInput, Select, Switch, TextInput } from "@mantine/core";
import type { CatalogField } from "./catalogTypes";

/** A single catalog field as a controlled Mantine input. `value` is always a string
 *  (multienum = comma-joined keys); onChange reports the new string value for the path. */
export function CatalogFieldInput({
  field, value, onChange, disabled,
}: {
  field: CatalogField;
  value: string;
  onChange: (path: string, value: string) => void;
  disabled: boolean;
}) {
  const label = field.label || field.path;
  const testid = `catalog-field-${field.path}`;
  const options = (field.options ?? []).map((o) => ({ value: o, label: o }));

  if (field.type === "bool") {
    return (
      <Switch
        label={label} data-testid={testid} disabled={disabled}
        checked={value === "1"}
        onChange={(e) => onChange(field.path, e.currentTarget.checked ? "1" : "0")} />
    );
  }
  if (field.type === "int") {
    return (
      <NumberInput
        label={label} data-testid={testid} disabled={disabled}
        value={value === "" ? "" : Number(value)}
        onChange={(v) => onChange(field.path, v === "" || v == null ? "" : String(v))} />
    );
  }
  if (field.type === "enum") {
    return (
      <Select
        label={label} data={options} data-testid={testid} disabled={disabled}
        value={value} onChange={(v) => onChange(field.path, v ?? "")} />
    );
  }
  if (field.type === "multienum") {
    const selected = value.split(",").filter(Boolean);
    return (
      <MultiSelect
        label={label} data={options} data-testid={testid} disabled={disabled}
        value={selected} onChange={(keys) => onChange(field.path, keys.join(","))} />
    );
  }
  return (
    <TextInput
      label={label} data-testid={testid} disabled={disabled}
      value={value} onChange={(e) => onChange(field.path, e.currentTarget.value)} />
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogFieldInput.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogFieldInput.tsx src/catalog/__tests__/catalogFieldInput.test.tsx
git commit -m "feat(catalog): CatalogFieldInput — field type to Mantine control"
```

---

### Task B5: `CatalogGridTable` (rows + add/edit/delete → grid ops)

**Files:**
- Create: `frontend/src/catalog/CatalogGridTable.tsx`
- Test: `frontend/src/catalog/__tests__/catalogGridTable.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogGridTable.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogGridTable } from "../CatalogGridTable";
import type { CatalogGrid, GridRow } from "../catalogTypes";

const GRID: CatalogGrid = {
  path: "hosts", endpoints: {},
  fields: [{ path: "hostname", type: "string" }],
};
const ROWS: GridRow[] = [{ uuid: "ab-12", hostname: "web" }];

describe("CatalogGridTable", () => {
  it("renders existing rows and emits a delete op", () => {
    const onOps = vi.fn();
    renderWithProviders(<CatalogGridTable grid={GRID} rows={ROWS} disabled={false} onOps={onOps} />);
    expect(screen.getByText("web")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("catalog-grid-hosts-del-ab-12"));
    expect(onOps).toHaveBeenCalledWith([{ op: "del", grid: "hosts", uuid: "ab-12" }]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogGridTable.test.tsx`
Expected: FAIL — cannot find `../CatalogGridTable`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/catalog/CatalogGridTable.tsx
import { useState } from "react";
import { Button, Group, Modal, Stack, Table, Text } from "@mantine/core";
import { useT } from "../i18n";
import { CatalogFieldInput } from "./CatalogFieldInput";
import type { CatalogGrid, CatalogGridOp, GridRow } from "./catalogTypes";

/** Editable table for one ArrayField grid. Tracks add/edit/delete against the live `rows`
 *  and reports the accumulated grid ops via onOps. Values are strings (see CatalogFieldInput). */
export function CatalogGridTable({
  grid, rows, disabled, onOps,
}: {
  grid: CatalogGrid;
  rows: GridRow[];
  disabled: boolean;
  onOps: (ops: CatalogGridOp[]) => void;
}) {
  const t = useT();
  const [ops, setOps] = useState<CatalogGridOp[]>([]);
  const [editing, setEditing] = useState<null | { uuid?: string; item: Record<string, string> }>(null);

  const push = (next: CatalogGridOp[]) => { const all = [...ops, ...next]; setOps(all); onOps(all); };
  const asString = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join(",") : (v ?? ""));

  function openAdd() {
    setEditing({ item: Object.fromEntries(grid.fields.map((f) => [f.path, ""])) });
  }
  function openEdit(row: GridRow) {
    setEditing({ uuid: row.uuid, item: Object.fromEntries(grid.fields.map((f) => [f.path, asString(row[f.path])])) });
  }
  function save() {
    if (!editing) return;
    push([editing.uuid
      ? { op: "set", grid: grid.path, uuid: editing.uuid, item: editing.item }
      : { op: "add", grid: grid.path, item: editing.item }]);
    setEditing(null);
  }

  return (
    <Stack gap="xs">
      <Table data-testid={`catalog-grid-${grid.path}`}>
        <Table.Thead>
          <Table.Tr>{grid.fields.map((f) => <Table.Th key={f.path}>{f.label || f.path}</Table.Th>)}<Table.Th /></Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.length === 0 && (
            <Table.Tr><Table.Td colSpan={grid.fields.length + 1}><Text c="dimmed">{t.catalog.grid.empty}</Text></Table.Td></Table.Tr>
          )}
          {rows.map((row) => (
            <Table.Tr key={row.uuid}>
              {grid.fields.map((f) => <Table.Td key={f.path}>{asString(row[f.path])}</Table.Td>)}
              <Table.Td>
                <Group gap="xs">
                  <Button size="xs" variant="light" disabled={disabled}
                    data-testid={`catalog-grid-${grid.path}-edit-${row.uuid}`} onClick={() => openEdit(row)}>
                    {t.catalog.grid.edit}
                  </Button>
                  <Button size="xs" color="red" variant="light" disabled={disabled}
                    data-testid={`catalog-grid-${grid.path}-del-${row.uuid}`}
                    onClick={() => push([{ op: "del", grid: grid.path, uuid: row.uuid }])}>
                    {t.catalog.grid.delete}
                  </Button>
                </Group>
              </Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Group>
        <Button size="xs" disabled={disabled} data-testid={`catalog-grid-${grid.path}-add`} onClick={openAdd}>
          {t.catalog.grid.add}
        </Button>
      </Group>
      <Modal opened={!!editing} onClose={() => setEditing(null)} title={grid.path}>
        <Stack>
          {editing && grid.fields.map((f) => (
            <CatalogFieldInput key={f.path} field={f} value={editing.item[f.path] ?? ""} disabled={false}
              onChange={(p, v) => setEditing({ ...editing, item: { ...editing.item, [p]: v } })} />
          ))}
          <Button onClick={save} data-testid={`catalog-grid-${grid.path}-save`}>{t.catalog.grid.add}</Button>
        </Stack>
      </Modal>
    </Stack>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogGridTable.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogGridTable.tsx src/catalog/__tests__/catalogGridTable.test.tsx
git commit -m "feat(catalog): CatalogGridTable — grid rows + add/edit/delete ops"
```

---

### Task B6: `CatalogModelForm` (scalars + grids + dirty-tracking + Propose)

**Files:**
- Create: `frontend/src/catalog/CatalogModelForm.tsx`
- Test: `frontend/src/catalog/__tests__/catalogModelForm.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogModelForm.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogModelForm } from "../CatalogModelForm";
import type { CatalogModelLive } from "../catalogTypes";

const LIVE: CatalogModelLive = {
  model: {
    id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
    fields: [{ path: "general.enabled", type: "bool" }, { path: "general.port", type: "int" }],
    grids: [], pages: [{ id: "general", fields: ["general.enabled", "general.port"] }],
  },
  values: { "general.enabled": "0", "general.port": "53" },
  grids: {}, reachable: true, read_only: false,
};

describe("CatalogModelForm", () => {
  it("proposes only changed scalars", async () => {
    const onPropose = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<CatalogModelForm live={LIVE} onPropose={onPropose} />);
    fireEvent.click(screen.getByTestId("catalog-field-general.enabled")); // 0 -> 1
    fireEvent.click(screen.getByTestId("catalog-propose"));
    await waitFor(() => expect(onPropose).toHaveBeenCalled());
    expect(onPropose).toHaveBeenCalledWith({
      model_id: "unbound", scalars: { "general.enabled": "1" }, grids: [],
    });
  });

  it("disables propose when read_only", () => {
    renderWithProviders(
      <CatalogModelForm live={{ ...LIVE, read_only: true }} onPropose={vi.fn()} />);
    expect(screen.queryByTestId("catalog-propose")).toBeNull();
    expect(screen.getByText(/safety denylist/i)).toBeInTheDocument();
  });

  it("shows the unreachable banner and no propose", () => {
    renderWithProviders(
      <CatalogModelForm live={{ ...LIVE, reachable: false, values: {} }} onPropose={vi.fn()} />);
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    expect(screen.queryByTestId("catalog-propose")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogModelForm.test.tsx`
Expected: FAIL — cannot find `../CatalogModelForm`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/catalog/CatalogModelForm.tsx
import { useMemo, useState } from "react";
import { Alert, Button, Stack, Text, Title } from "@mantine/core";
import { useT } from "../i18n";
import { CatalogFieldInput } from "./CatalogFieldInput";
import { CatalogGridTable } from "./CatalogGridTable";
import type { CatalogChangeBody, CatalogField, CatalogGridOp, CatalogModelLive } from "./catalogTypes";

const toStr = (v: string | string[] | undefined) => (Array.isArray(v) ? v.join(",") : (v ?? ""));

export function CatalogModelForm({
  live, onPropose,
}: {
  live: CatalogModelLive;
  onPropose: (body: CatalogChangeBody) => Promise<unknown>;
}) {
  const t = useT();
  const { model, values, grids, reachable, read_only } = live;
  const editable = reachable && !read_only;

  // Seed working scalar state from the live values (all as strings).
  const seeded = useMemo(() => {
    const s: Record<string, string> = {};
    for (const f of model.fields) s[f.path] = toStr(values[f.path]);
    return s;
  }, [model, values]);
  const [work, setWork] = useState<Record<string, string>>(seeded);
  const [gridOps, setGridOps] = useState<Record<string, CatalogGridOp[]>>({});

  const fieldByPath = useMemo(() => {
    const m = new Map<string, CatalogField>();
    for (const f of model.fields) m.set(f.path, f);
    for (const g of model.grids) for (const f of g.fields) m.set(`${g.path}.${f.path}`, f);
    return m;
  }, [model]);

  function build(): CatalogChangeBody {
    const scalars: Record<string, string> = {};
    for (const [path, val] of Object.entries(work)) if (val !== seeded[path]) scalars[path] = val;
    const ops = Object.values(gridOps).flat();
    return { model_id: model.id, scalars, grids: ops };
  }

  if (read_only) {
    return <Alert color="yellow">{t.catalog.readOnly}</Alert>;
  }

  return (
    <Stack>
      <Title order={4}>{model.title}</Title>
      {!reachable && <Alert color="red">{t.catalog.unreachable}</Alert>}
      {model.pages.map((page) => (
        <Stack key={page.id} gap="xs">
          <Text fw={600}>{page.id}</Text>
          {page.fields.map((path) => {
            const f = fieldByPath.get(path);
            if (!f) return null;
            return (
              <CatalogFieldInput key={path} field={f} value={work[path] ?? ""} disabled={!editable}
                onChange={(p, v) => setWork((w) => ({ ...w, [p]: v }))} />
            );
          })}
        </Stack>
      ))}
      {model.grids.map((g) => (
        <Stack key={g.path} gap="xs">
          <Text fw={600}>{g.path}</Text>
          <CatalogGridTable grid={g} rows={grids[g.path] ?? []} disabled={!editable}
            onOps={(ops) => setGridOps((m) => ({ ...m, [g.path]: ops }))} />
        </Stack>
      ))}
      {editable && (
        <Button data-testid="catalog-propose" onClick={() => onPropose(build())}>
          {t.catalog.propose}
        </Button>
      )}
    </Stack>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogModelForm.test.tsx`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogModelForm.tsx src/catalog/__tests__/catalogModelForm.test.tsx
git commit -m "feat(catalog): CatalogModelForm — scalars + grids, dirty-diff propose"
```

---

### Task B7: `CatalogEditorTab` (model list + selection + form)

**Files:**
- Create: `frontend/src/catalog/CatalogEditorTab.tsx`
- Test: `frontend/src/catalog/__tests__/catalogEditorTab.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/catalog/__tests__/catalogEditorTab.test.tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { CatalogEditorTab } from "../CatalogEditorTab";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider value={{ tenants: [], activeId: "t1", setActiveId: () => {}, loading: false }}>
      {node}
    </TenantContext.Provider>
  );
}

const CATALOG = {
  resolved_version: "26.1.8",
  models: {
    unbound: { id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
               fields: [{ path: "general.enabled", type: "bool" }], grids: [],
               pages: [{ id: "general", fields: ["general.enabled"] }], read_only: false },
  },
};

describe("CatalogEditorTab", () => {
  it("lists models and opens one", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog", () => HttpResponse.json(CATALOG)),
      http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound", () =>
        HttpResponse.json({ model: CATALOG.models.unbound, values: { "general.enabled": "1" },
                            grids: {}, reachable: true, read_only: false })),
    );
    renderWithProviders(withTenant(<CatalogEditorTab deviceId="d1" />));
    await waitFor(() => expect(screen.getByText("Unbound")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Unbound"));
    await waitFor(() => expect(screen.getByTestId("catalog-field-general.enabled")).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogEditorTab.test.tsx`
Expected: FAIL — cannot find `../CatalogEditorTab`.

- [ ] **Step 3: Write the component**

```tsx
// frontend/src/catalog/CatalogEditorTab.tsx
import { useMemo, useState } from "react";
import { Badge, Card, Grid, Loader, NavLink, ScrollArea, Stack, Text, TextInput } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useT } from "../i18n";
import { CatalogModelForm } from "./CatalogModelForm";
import { useCatalogModel, useDeviceCatalog, useProposeCatalogChange } from "./catalogHooks";
import type { CatalogChangeBody } from "./catalogTypes";

export function CatalogEditorTab({ deviceId }: { deviceId: string }) {
  const t = useT();
  const catalog = useDeviceCatalog(deviceId);
  const [selected, setSelected] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const model = useCatalogModel(deviceId, selected);
  const propose = useProposeCatalogChange(deviceId);

  const models = useMemo(() => {
    const all = Object.values(catalog.data?.models ?? {});
    const q = search.trim().toLowerCase();
    return all
      .filter((m) => !q || m.id.toLowerCase().includes(q) || (m.title ?? "").toLowerCase().includes(q))
      .sort((a, b) => (a.title ?? a.id).localeCompare(b.title ?? b.id));
  }, [catalog.data, search]);

  async function onPropose(body: CatalogChangeBody) {
    if (Object.keys(body.scalars).length === 0 && body.grids.length === 0) {
      notifications.show({ message: t.catalog.noChanges });
      return;
    }
    try {
      await propose.mutateAsync(body);
      notifications.show({ message: t.catalog.proposed });
    } catch {
      notifications.show({ color: "red", message: t.catalog.proposeFailed });
    }
  }

  if (catalog.isLoading) return <Loader />;
  if (!catalog.data || Object.keys(catalog.data.models).length === 0) {
    return <Text c="dimmed">{t.catalog.noModels}</Text>;
  }

  return (
    <Grid>
      <Grid.Col span={{ base: 12, sm: 4 }}>
        <Stack gap="xs">
          <TextInput placeholder={t.catalog.searchModels} value={search}
            onChange={(e) => setSearch(e.currentTarget.value)} data-testid="catalog-search" />
          <ScrollArea h={500}>
            {models.map((m) => (
              <NavLink key={m.id} label={m.title || m.id} active={selected === m.id}
                onClick={() => setSelected(m.id)}
                rightSection={m.read_only ? <Badge size="xs" color="gray">RO</Badge> : null} />
            ))}
          </ScrollArea>
        </Stack>
      </Grid.Col>
      <Grid.Col span={{ base: 12, sm: 8 }}>
        <Card withBorder>
          {!selected && <Text c="dimmed">{t.catalog.selectModel}</Text>}
          {selected && model.isLoading && <Loader />}
          {selected && model.data && <CatalogModelForm live={model.data} onPropose={onPropose} />}
        </Card>
      </Grid.Col>
    </Grid>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/catalog/__tests__/catalogEditorTab.test.tsx`
Expected: PASS

> If `@mantine/notifications` is not already a dependency, check `frontend/package.json`. The project
> uses Mantine v9; notifications ship as `@mantine/notifications`. If absent, replace the two
> `notifications.show(...)` calls with a local `useState` message + an inline `<Text>` (no new dep).
> Verify with `grep -r "@mantine/notifications" frontend/src | head` before writing the component.

- [ ] **Step 5: Commit**

```bash
cd frontend && git add src/catalog/CatalogEditorTab.tsx src/catalog/__tests__/catalogEditorTab.test.tsx
git commit -m "feat(catalog): CatalogEditorTab — searchable model list + live form + propose"
```

---

### Task B8: Wire the "Editor" tab into the device page

**Files:**
- Modify: `frontend/src/pages/DeviceDetailPage.tsx`
- Test: `frontend/src/pages/__tests__/deviceDetailEditorTab.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/__tests__/deviceDetailEditorTab.test.tsx
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { DeviceDetailPage } from "../DeviceDetailPage";

describe("DeviceDetailPage editor tab", () => {
  it("shows an Editor tab", () => {
    renderWithProviders(<DeviceDetailPage />, { route: "/devices/d1" });
    expect(screen.getByText("Editor")).toBeInTheDocument();
  });
});
```

> Check how the existing `DeviceDetailPage` tests render the page (route param + any MSW handlers for
> the device fetch) in `frontend/src/pages/__tests__/` and mirror that setup; the device id comes from
> the route. If the page needs a device fetch handler to render the tab list, add the same handler the
> existing device-detail test uses.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm run test -- src/pages/__tests__/deviceDetailEditorTab.test.tsx`
Expected: FAIL — no "Editor" tab yet.

- [ ] **Step 3: Add the tab**

In `frontend/src/pages/DeviceDetailPage.tsx`:
- import: `import { CatalogEditorTab } from "../catalog/CatalogEditorTab";`
- add a tab trigger after the `templates` tab:
  ```tsx
  <Tabs.Tab value="editor">{t.catalog.tab}</Tabs.Tab>
  ```
- add the panel after the `templates` panel:
  ```tsx
  <Tabs.Panel value="editor" pt="md">
    {deviceId && <CatalogEditorTab deviceId={deviceId} />}
  </Tabs.Panel>
  ```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm run test -- src/pages/__tests__/deviceDetailEditorTab.test.tsx`
Expected: PASS

- [ ] **Step 5: Build gate + commit**

```bash
cd frontend && npm run build && npm run lint
git add src/pages/DeviceDetailPage.tsx src/pages/__tests__/deviceDetailEditorTab.test.tsx
git commit -m "feat(catalog): add the Editor tab to the device page"
```

---

## Final verification

- [ ] **Backend suite + lint**

```bash
cd backend && export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test" ADMIN_DATABASE_URL="$TEST_DATABASE_URL"
.venv/bin/pytest -q && .venv/bin/ruff check app/
```
Expected: all green; ruff clean.

- [ ] **Frontend tests + build gate**

```bash
cd frontend && npm run test && npm run build && npm run lint
```
Expected: all green (the build gate is `tsc -b` + `vite build`, per the frontend-build-gate note).

---

## Self-review (controller — done at plan-write time)

**Spec coverage:**
- Live model endpoint (schema + live values, reachable/read_only) → A4 (+ A2/A3 pure fns, A1 shared helper). ✓
- Reuse of option-object normalization → A1 (shared `opnsense_values`). ✓
- Editor tab, model list + search, generated form, grids, propose via existing pipeline → B3–B8. ✓
- Field type → input mapping → B4. ✓
- Read-only/denylist + unreachable states → B6 (form) + A4 (endpoint). ✓
- Propose sends only changed scalars + explicit grid ops → B6 (`build()` dirty-diff) + B5 (ops). ✓
- Testing matrix (pure fns, endpoint, hooks, field input, grid, form, tab, wiring) → covered. ✓

**Type consistency:** `CatalogModelLive`/`CatalogChangeBody`/`CatalogGridOp` defined in B3 are used unchanged in B4–B7. The endpoint response shape in A4 matches `CatalogModelLive` (model/values/grids/reachable/read_only). `scalars` are strings everywhere; `multienum` comma-joined in B4 and split back in B4/B5. The propose body `{model_id, scalars, grids}` matches the backend `CatalogChangeIn` (#94).

**Known follow-ups (out of 3a, per spec):** dynamic `ref` option lists, OPNsense-like menu nav + global search (3b); cross-version diff badges + live config.xml map (3c). The grid wrapper `row` key defaulting (from #94) is unchanged here.

**Risk flags for the implementer:**
- B1 (gen:api) must run with the backend venv + env importable; it regenerates types for the #94 catalog endpoints too (they were backend-only).
- B7 notes the `@mantine/notifications` dependency check.
- B8 notes mirroring the existing DeviceDetailPage test setup (device fetch handler).
