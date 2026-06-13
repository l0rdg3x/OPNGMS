# Plugin Coverage — Phase 4b Implementation Plan (edit a plugin's config)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator edit an **installed plugin's configuration** straight from the Plugins page — a "Configure" button opens a drawer that reuses the existing catalog model form + the gated `catalog_setting` apply pipeline.

**Architecture:** The plugins catalog (Phase 1) carries each plugin's editable config model; the editor's model-fetch + change endpoints currently only look in the *core* catalog. This phase (a) makes those two endpoints fall back to the **plugins** catalog so a plugin model is loadable + editable, (b) adds a small endpoint mapping each configurable plugin `package → model_id`, and (c) on the Plugins page renders a **Configure** drawer reusing `CatalogModelForm` + `useCatalogModel` + `useProposeCatalogChange`. No new apply path — the generic `catalog_setting` change kind already writes any model. No change to the main config editor (`CatalogEditorTab`) or its menu, so it isn't flooded with ~150 plugin models.

**Tech Stack:** FastAPI + the `catalog_provider` (`get_catalog` / `get_plugins_catalog` from Phase 1), pytest + respx/httpx; React 19 + Mantine `Drawer`, TanStack Query, vitest + msw.

**Branch:** `feat/plugin-config` (already created off `main`).

**Spec:** `docs/superpowers/specs/2026-06-13-plugin-catalog-coverage-design.md` (Phase 4 — editor integration).

**Backend test env** (TimescaleDB up; schema via `create_all`):
```
cd /home/l0rdg3x/coding/OPNGMS/backend && . .venv/bin/activate
export ADMIN_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
export TEST_DATABASE_URL="postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test"
```
Backend lint: `ruff check app/`. Frontend gate: `npm run build` (from `frontend/`).

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `backend/app/api/catalog.py` | NEW `_catalog_model` helper (core→plugins fallback); use it in model-fetch + change; NEW `GET .../plugin-models` | Modify |
| `backend/app/schemas/catalog.py` | NEW `PluginModelOut` | Modify |
| `backend/tests/test_plugin_config_api.py` | Tests: plugin model fetch/change fallback + plugin-models map | Create |
| `frontend/src/api/schema.d.ts` | Regenerate (new endpoint) | Regenerate |
| `frontend/src/plugins/pluginsHooks.ts` | NEW `usePluginModels(deviceId)` | Modify |
| `frontend/src/plugins/PluginsTab.tsx` | NEW Configure button (installed + has-model) + config drawer | Modify |
| `frontend/src/i18n/en.ts` + 11 locales | `plugins.configure` + `plugins.configureTitle` | Modify |
| `frontend/src/plugins/__tests__/pluginsTab.test.tsx` | Configure-drawer test | Modify |

---

## Task 1: Backend — resolve plugin models in the model-fetch + change endpoints

**Files:**
- Modify: `backend/app/api/catalog.py`
- Test: `backend/tests/test_plugin_config_api.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_plugin_config_api.py` (mirrors the existing catalog-API test style; it seeds a cached core + plugins catalog directly into `catalog_cache` so no network is needed, and stubs the device's live read by pointing it at an unreachable base_url so the model returns schema-only `reachable:false`):

```python
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.catalog_cache import CatalogCache
from tests.factories import make_tenant

_CORE = {"edition": "community", "version": "26.1.9", "models": {
    "ids": {"id": "ids", "source": "core", "model_root": "ids", "xml_path": "OPNsense/IDS",
            "endpoints": {"get": "ids/settings/get"}, "fields": [], "grids": [], "pages": []}}, "menu": []}
_PLUGINS = {"edition": "community", "version": "26.1.9", "models": {
    "haproxy": {"id": "haproxy", "source": "plugins", "model_root": "haproxy",
                "xml_path": "OPNsense/HAProxy/general", "endpoints": {"get": "haproxy/settings/get"},
                "fields": [], "grids": [], "pages": [],
                "plugin": {"package": "os-haproxy", "title": "HAProxy", "category": "net", "version": "5.1"}}},
    "menu": []}


async def _setup(api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="acme")
        tid = t.id
        s.add(CatalogCache(edition="community", version="26.1.9", sha256="a", content=_CORE))
        s.add(CatalogCache(edition="community-plugins", version="26.1.9", sha256="b", content=_PLUGINS))
        did = uuid.uuid4()
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls,"
            " status, tags, edition, firmware_version) VALUES (:id,:t,'fw','https://127.0.0.1:1',"
            "''::bytea,''::bytea,true,'reachable','{}','community','26.1.9')"), {"id": did, "t": tid})
        await s.commit()
    await api_client.post("/api/setup", json={"email": "sa@x.io", "name": "SA", "password": "pw12345-secure"})
    await api_client.post("/api/login", json={"email": "sa@x.io", "password": "pw12345-secure"})
    return tid, did


async def test_plugin_model_fetch_falls_back_to_plugins_catalog(api_client, db_engine, monkeypatch):
    # auto_fetch off so the provider serves only the cached catalogs.
    monkeypatch.setenv("CATALOG_AUTO_FETCH", "false")
    tid, did = await _setup(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/catalog/models/haproxy")
    assert r.status_code == 200
    assert r.json()["model"]["plugin"]["package"] == "os-haproxy"


async def test_plugin_models_map_lists_configurable_plugins(api_client, db_engine, monkeypatch):
    monkeypatch.setenv("CATALOG_AUTO_FETCH", "false")
    tid, did = await _setup(api_client, db_engine)
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/plugin-models")
    assert r.status_code == 200
    assert r.json() == [{"package": "os-haproxy", "model_id": "haproxy", "title": "HAProxy"}]
```

> If `CATALOG_AUTO_FETCH` is not the exact settings env var, grep `backend/app/core/config.py` for the catalog auto-fetch flag and set it accordingly (the goal: the provider must NOT hit the network and must serve the cached rows). Alternatively pass `auto_fetch=False` — but the API path can't, so the env/settings toggle is the lever.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_plugin_config_api.py -q`
Expected: FAIL — `haproxy` 404 on the model fetch (core-only lookup) + the `plugin-models` route is 404 (undefined).

- [ ] **Step 3: Implement the helper + wire it + the map endpoint**

In `backend/app/api/catalog.py`, add the helper (after `_load_device`):

```python
async def _catalog_model(session: AsyncSession, device: Device, model_id: str) -> dict | None:
    """A model's schema from the device's core catalog, falling back to its plugins catalog."""
    core = await catalog_provider.get_catalog(session, device.edition, device.firmware_version or "")
    model = (core or {}).get("models", {}).get(model_id)
    if model is not None:
        return model
    plugins = await catalog_provider.get_plugins_catalog(
        session, device.edition, device.firmware_version or "")
    return (plugins or {}).get("models", {}).get(model_id)
```

In `read_catalog_model`, replace the catalog-load + lookup (the `catalog = ...get_catalog...` / `if catalog is None` / `model = catalog.get(...)` block) with:

```python
    device = await _load_device(session, tenant_id, device_id)
    model = await _catalog_model(session, device, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"unknown model: {model_id!r}")
```

(Leave everything below — the `base = {...}`, the denylist check, the live `OpnsenseClient` read — unchanged.)

In `create_catalog_change`, replace its catalog-load + lookup (the `catalog = ...get_catalog...` / `if catalog is None` / denylist / `model = catalog.get(...)` block) with:

```python
    device = await _load_device(session, tenant_id, device_id)
    if body.model_id in CATALOG_DENYLIST:
        raise HTTPException(status_code=422, detail=f"model {body.model_id!r} is not editable (safety denylist)")
    model = await _catalog_model(session, device, body.model_id)
    if model is None:
        raise HTTPException(status_code=422, detail=f"unknown model: {body.model_id!r}")
```

(Leave `_build_payload(model, body)` and below unchanged.)

Add the map endpoint (after `read_catalog_model`), and import the schema:

```python
from app.schemas.catalog import CatalogChangeIn, PluginModelOut
```

```python
@router.get("/devices/{device_id}/plugin-models", response_model=list[PluginModelOut])
async def read_plugin_models(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Plugins that have an editable config model: [{package, model_id, title}] (for the Configure link)."""
    device = await _load_device(session, tenant_id, device_id)
    plugins = await catalog_provider.get_plugins_catalog(
        session, device.edition, device.firmware_version or "")
    out: list[dict] = []
    for model_id, m in (plugins or {}).get("models", {}).items():
        pl = m.get("plugin") or {}
        if pl.get("package"):
            out.append({"package": pl["package"], "model_id": model_id, "title": pl.get("title", "")})
    return out
```

- [ ] **Step 4: Add the schema**

In `backend/app/schemas/catalog.py`, add:

```python
class PluginModelOut(BaseModel):
    package: str
    model_id: str
    title: str = ""
```

(Confirm `BaseModel` is already imported in that file; if not, `from pydantic import BaseModel`.)

- [ ] **Step 5: Run to verify it passes**

Run: `python -m pytest tests/test_plugin_config_api.py -q`
Expected: PASS (both tests).

- [ ] **Step 6: Regression + lint + commit**

Run: `python -m pytest tests/ -k "catalog or plugin" -q` → green. `ruff check app/` → clean.

```bash
git add backend/app/api/catalog.py backend/app/schemas/catalog.py backend/tests/test_plugin_config_api.py
git commit -m "feat(catalog): editor resolves plugin models + GET device/{id}/plugin-models"
```

---

## Task 2: Frontend — regenerate types + `usePluginModels`

**Files:**
- Regenerate: `frontend/src/api/schema.d.ts`
- Modify: `frontend/src/plugins/pluginsHooks.ts`

- [ ] **Step 1: Regenerate the client**

From `frontend/`: `npm run gen:api`. Verify: `grep -n "plugin-models" src/api/schema.d.ts` matches.

- [ ] **Step 2: Add the hook**

Append to `frontend/src/plugins/pluginsHooks.ts`:

```tsx
export type PluginModel = components["schemas"]["PluginModelOut"];

/** Map of plugin package -> its editable config model id (plugins that have a config model). */
export function usePluginModels(deviceId: string) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["plugin-models", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<PluginModel[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/plugin-models",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) return [];   // configurability is optional enrichment — degrade quietly
      return data;
    },
  });
}
```

- [ ] **Step 3: Type-check + commit**

Run: `npx tsc -b` (clean after Task 4 i18n if a key is referenced early — none here). Commit:

```bash
git add frontend/src/api/schema.d.ts frontend/openapi.json frontend/src/plugins/pluginsHooks.ts
git commit -m "feat(plugins): usePluginModels — package -> config model map"
```

---

## Task 3: Frontend — the Configure drawer on the Plugins page

**Files:**
- Modify: `frontend/src/plugins/PluginsTab.tsx`

Add, per **installed** plugin whose package has a config model, a "Configure" button that opens a Mantine `Drawer` reusing `CatalogModelForm`.

- [ ] **Step 1: Wire the map + a configure target**

In `PluginsTab.tsx`, add imports + state:

```tsx
import { Drawer } from "@mantine/core";  // add Drawer to the existing @mantine/core import
import { CatalogModelForm } from "../catalog/CatalogModelForm";
import { useCatalogModel, useProposeCatalogChange } from "../catalog/catalogHooks";
import type { CatalogChangeBody } from "../catalog/catalogTypes";
import { usePluginModels } from "./pluginsHooks";   // add to the existing ./pluginsHooks import
```

Inside the component (after `const create = ...`):

```tsx
  const models = usePluginModels(deviceId);
  const modelByPkg = useMemo(
    () => new Map((models.data ?? []).map((m) => [m.package, m.model_id])),
    [models.data]);
  const [configureModel, setConfigureModel] = useState<string | null>(null);
  const live = useCatalogModel(deviceId, configureModel);
  const propose = useProposeCatalogChange(deviceId);

  async function onPropose(body: CatalogChangeBody) {
    try {
      await propose.mutateAsync(body);
      notifications.show({ message: t.catalog.proposed });
      setConfigureModel(null);
    } catch {
      notifications.show({ color: "red", message: t.catalog.proposeFailed });
    }
  }
```

- [ ] **Step 2: Add the Configure button in the actions cell**

In the row actions `<Table.Td>`, alongside the Install/Remove button, add (a Configure button shows for an **installed** plugin that has a model — independent of `canWrite`, since proposing is itself gated server-side by CONFIG_PUSH, but keep it behind `canWrite` for a consistent UI):

```tsx
                  {canWrite && p.installed && modelByPkg.has(p.name) && (
                    <Button size="xs" variant="light" ml="xs"
                      data-testid={`plugin-configure-${p.name}`}
                      onClick={() => setConfigureModel(modelByPkg.get(p.name)!)}>
                      {t.plugins.configure}
                    </Button>
                  )}
```

- [ ] **Step 3: Add the drawer (before the closing `</Card>`, after the existing confirm `<Modal>`)**

```tsx
      <Drawer opened={configureModel !== null} onClose={() => setConfigureModel(null)}
              position="right" size="xl" title={t.plugins.configureTitle}>
        {live.isLoading && <Loader />}
        {live.data && <CatalogModelForm live={live.data} onPropose={onPropose} />}
      </Drawer>
```

- [ ] **Step 4: Build**

Run (from `frontend/`): `npm run build`
Expected: success (after Task 4 adds the two new i18n keys to all locales — do Task 4 first if `t.plugins.configure*` errors).

- [ ] **Step 5: Commit** (after Task 4)

```bash
git add frontend/src/plugins/PluginsTab.tsx
git commit -m "feat(plugins): Configure drawer to edit an installed plugin's config"
```

---

## Task 4: i18n — two new keys across 12 locales

**Files:**
- Modify: `frontend/src/i18n/en.ts` + the 11 sibling locales

- [ ] **Step 1: Add to `en.ts`** (inside the existing `plugins` group):

```ts
    configure: "Configure",
    configureTitle: "Plugin configuration",
```

- [ ] **Step 2: Mirror (translated) into all 11 locales** (`it es fr de pt nl ru ar zh zhTW ja`). Keys must match `en.ts` exactly (compiler-enforced). Use each locale's existing `plugins`/`catalog` strings as the style reference; keep proper diacritics/scripts.

- [ ] **Step 3: Type-check + commit**

Run: `npx tsc -b` → clean.

```bash
git add frontend/src/i18n
git commit -m "i18n(plugins): Configure drawer strings across all 12 locales"
```

---

## Task 5: Frontend test — the Configure drawer

**Files:**
- Modify: `frontend/src/plugins/__tests__/pluginsTab.test.tsx`

- [ ] **Step 1: Add the test**

Append inside the `describe("PluginsTab", ...)` block (the `PLUGINS_MODELS`/`MODEL` routes mirror the new endpoints):

```tsx
  it("shows Configure for an installed plugin with a config model and opens the drawer", async () => {
    server.use(
      http.get(PLUGINS, () => HttpResponse.json(SAMPLE)),
      http.get("/api/tenants/t1/devices/d1/plugin-models",
        () => HttpResponse.json([{ package: "os-wireguard", model_id: "wireguard", title: "WireGuard" }])),
      http.get("/api/tenants/t1/devices/d1/catalog/models/wireguard", () => HttpResponse.json({
        model: { id: "wireguard", title: "WireGuard", fields: [], grids: [], pages: [], endpoints: {} },
        values: {}, grids: {}, field_options: {}, grid_field_options: {}, reachable: true, read_only: false })),
    );
    renderWithProviders(withTenant(<PluginsTab deviceId="d1" />));
    // os-wireguard is installed + has a model -> Configure shows; os-acme-client (not installed) does not.
    const cfg = await screen.findByTestId("plugin-configure-os-wireguard");
    expect(screen.queryByTestId("plugin-configure-os-acme-client")).not.toBeInTheDocument();
    await userEvent.click(cfg);
    expect(await screen.findByText("WireGuard")).toBeInTheDocument();   // the form title renders in the drawer
  });
```

> If `CatalogModelForm` renders the title differently (e.g. not as plain text "WireGuard"), adjust the assertion to a stable element it does render (read `src/catalog/CatalogModelForm.tsx` for a `data-testid` like `catalog-propose`, which the drawer will show). Prefer asserting `findByTestId("catalog-propose")` if the title text is ambiguous.

- [ ] **Step 2: Run + commit**

Run: `npx vitest run src/plugins` → green.

```bash
git add frontend/src/plugins/__tests__/pluginsTab.test.tsx
git commit -m "test(plugins): Configure drawer opens the plugin config form"
```

---

## Final verification (before opening the Phase 4b PR)

- [ ] **Backend:** `cd backend && ruff check app/` clean; `python -m pytest tests/ -k "catalog or plugin or device" -q` green.
- [ ] **Frontend:** `cd frontend && npm run build` success; `npm run lint` clean; `npx vitest run` green.
- [ ] Open the Phase 4b PR; CI green; squash-merge. **The plugin milestone is then complete** (catalog → telemetry → install/remove → Plugins page → config editing). A small follow-up cleanup can remove the now-redundant raw plugin install/remove inputs from `FirmwareActions.tsx`.

---

## Self-review notes (author)

- **Reuse:** plugin config edits flow through the SAME `catalog_setting` change kind + the SAME `CatalogModelForm` + `useProposeCatalogChange`. The only backend change is a catalog *lookup* fallback (core→plugins) on two endpoints + a tiny read-only map endpoint.
- **No editor flood:** the main `CatalogEditorTab` menu is untouched (still core-only); plugin config lives on the Plugins page drawer where the plugin does.
- **Gating:** Configure shows only for installed plugins with a model; proposing is server-gated by `Action.CONFIG_PUSH` (unchanged), and the denylist still applies in `create_catalog_change`.
- **Security:** no new outbound path; the plugin model's live read reuses the existing SSRF-guarded `OpnsenseClient` in `read_catalog_model`. Tenant isolation unchanged (same `_load_device` + RLS).
- **Type consistency:** `PluginModelOut {package, model_id, title}` is the single shape across the endpoint, `usePluginModels`, and the `modelByPkg` map.
