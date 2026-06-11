# Config Templates M3 — `monit_test` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the curated `monit_test` config-template kind — a portable Monit health-check test (condition + action), introspection-driven and value-controlled, upserted by `name`.

**Architecture:** Reuses the kind engine + the introspection machinery with NO engine changes (a monit test is fully portable — no apply-time binding). Mirrors `firewall_rule` minus the interface binding. Connector upserts the test by `name` via `monit/settings` addTest/setTest + `monit/service/reconfigure`.

**Tech Stack:** Python 3.14, FastAPI async, httpx, respx, pytest. Vite + React 19 + Mantine v9 + TanStack Query v5 + openapi-fetch + Vitest/RTL/MSW.

**Verified API (real box 26.1.9, read + revertible write):** `GET monit/settings/getTest`→`{test:{name,type,condition,action,path}}` (type/action option-objects); `GET monit/settings/searchTest`→`{rows:[{uuid,name,…}]}`; `POST monit/settings/addTest {"test":{…}}`→`{result:"saved",uuid}`; `setTest/{uuid}`; `delTest/{uuid}`; `POST monit/service/reconfigure`→`{status:"ok"}`.

**Conventions:** venv `/home/l0rdg3x/coding/OPNGMS/backend/.venv/bin/python`; pytest needs `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`; CI lint gate is `ruff check app/` only. Mirror `app/services/ids_kind.py`, `app/api/ids.py`, `app/services/firewall_introspect.py`, `frontend/src/templates/FirewallRuleForm.tsx` + `frontend/src/templates/__tests__/firewallRuleForm.test.tsx`. English everywhere.

---

## BACKEND (→ PR 1)

### Task 1: Connector — `get_monit_test_model` + `apply_monit_test` (upsert by name)

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Test `backend/tests/test_monit_connector.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_monit_connector.py`

```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_monit_test_model_returns_test():
    respx.get(url__regex=r".*/api/monit/settings/getTest.*").mock(
        return_value=httpx.Response(200, json={"test": {"action": {"alert": {"value": "alert", "selected": 0}}}}))
    model = await _c().get_monit_test_model()
    assert "action" in model


@respx.mock
async def test_apply_monit_test_add_then_reconfigure():
    respx.get(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    posts = []
    def _cap(request):
        posts.append(str(request.url).split("/api/")[1]); return httpx.Response(200, json={"result": "saved", "uuid": "u1"})
    respx.post(url__regex=r".*/api/monit/settings/addTest.*").mock(side_effect=_cap)
    rec = respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test(
        "set", {"name": "CPUHigh", "type": "SystemResource", "condition": "cpu usage is greater than 90%", "action": "alert"}, dry_run=False)
    assert any(p.startswith("monit/settings/addTest") for p in posts)
    assert rec.called and res["operation"] == "add" and res["dry_run"] is False


@respx.mock
async def test_apply_monit_test_upsert_sets_existing():
    respx.get(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "u9", "name": "CPUHigh"}, {"uuid": "uX", "name": "Other"}]}))
    setp = respx.post(url__regex=r".*/api/monit/settings/setTest/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    respx.post(url__regex=r".*/api/monit/service/reconfigure.*").mock(return_value=httpx.Response(200, json={"status": "ok"}))
    res = await _c().apply_monit_test("set", {"name": "CPUHigh", "action": "alert"}, dry_run=False)
    assert setp.called and res["operation"] == "set"


@respx.mock
async def test_apply_monit_test_ambiguous_refuses():
    respx.get(url__regex=r".*/api/monit/settings/searchTest.*").mock(
        return_value=httpx.Response(200, json={"rows": [{"uuid": "a", "name": "dup"}, {"uuid": "b", "name": "dup"}]}))
    add = respx.post(url__regex=r".*/api/monit/settings/addTest.*")
    with pytest.raises(ApiError):
        await _c().apply_monit_test("set", {"name": "dup"}, dry_run=False)
    assert not add.called


@respx.mock
async def test_apply_monit_test_dry_run_writes_nothing():
    search = respx.get(url__regex=r".*/api/monit/settings/searchTest.*")
    add = respx.post(url__regex=r".*/api/monit/settings/addTest.*")
    res = await _c().apply_monit_test("set", {"name": "x"}, dry_run=True)
    assert not search.called and not add.called and res["dry_run"] is True
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** in `client.py` (place after `apply_firewall_rule`/`_resolve_rule_uuid`, before `list_ids_rulesets`):

```python
    async def get_monit_test_model(self) -> dict:
        """Blank Monit test model (option-objects/strings) for the introspection form."""
        return (await self._get("monit/settings/getTest")).get("test", {})

    async def apply_monit_test(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Upsert a Monit test by `name`, then reconfigure monit.

        Verified against OPNsense 26.1.9: monit/settings addTest/setTest/{uuid} + monit/service/reconfigure.
        Identity is `name`: 1 match -> setTest; none -> addTest; many -> refuse (never mutate on doubt).
        dry_run performs NO mutation."""
        name = str(payload.get("name", ""))
        if dry_run:
            return {"dry_run": True, "name": name}
        uuid_ = await self._resolve_monit_test_uuid(name)
        if uuid_ is None:
            res = await self._post("monit/settings/addTest", {"test": payload})
            op = "add"
        else:
            res = await self._post(f"monit/settings/setTest/{uuid_}", {"test": payload})
            op = "set"
        await self._post("monit/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": op, "result": res}

    async def _resolve_monit_test_uuid(self, name: str) -> str | None:
        """Resolve a Monit test by EXACT name. None if absent; ApiError if many (never mutate on doubt)."""
        if not name:
            raise ApiError(0, "monit test name required (it is the test identity)")
        data = await self._post(
            "monit/settings/searchTest", {"current": 1, "rowCount": 1000, "searchPhrase": name})
        matches = [r for r in data.get("rows", []) if r.get("name") == name]
        if len(matches) > 1:
            raise ApiError(0, f"monit test '{name}' not uniquely resolvable ({len(matches)} matches)")
        return matches[0]["uuid"] if matches else None
```

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/connectors/opnsense/client.py
git add app/connectors/opnsense/client.py tests/test_monit_connector.py
git commit -m "feat(monit): connector get_monit_test_model + upsert apply_monit_test"
```

---

### Task 2: Introspection + `monit_test` kind + applier + registration

**Files:** Create `backend/app/services/monit_introspect.py`, `backend/app/services/monit_kind.py`; Modify `backend/app/main.py`, `backend/app/worker.py`; Test `backend/tests/test_monit_introspect.py`, `backend/tests/test_monit_kind.py`

- [ ] **Step 1: Failing tests**

`backend/tests/test_monit_introspect.py`:
```python
from app.services.monit_introspect import infer_test_fields

_MODEL = {"test": {
    "name": "",
    "type": {"SystemResource": {"value": "SystemResource", "selected": 1},
             "Existence": {"value": "Existence", "selected": 0}},
    "condition": "",
    "action": {"alert": {"value": "alert", "selected": 0}},
    "path": "",
}}


def test_infer_test_fields_classifies_controls():
    out = infer_test_fields(_MODEL)
    paths = {f["path"]: f["control"] for f in out["fields"]}
    assert paths["type"] == "select" and paths["action"] == "select"
    assert paths["name"] == "text" and paths["condition"] == "text" and paths["path"] == "text"
```

`backend/tests/test_monit_kind.py`:
```python
import pytest

import app.services.monit_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl

_GOOD = {"name": "CPUHigh", "type": "SystemResource",
         "condition": "cpu usage is greater than 90%", "action": "alert", "path": ""}


def test_monit_test_kind_registered():
    spec = tpl.TEMPLATE_KINDS["monit_test"]
    assert spec.change_kind == "monit_test"
    op, target, payload = spec.to_change(_GOOD)
    assert op == "set" and target == "CPUHigh" and payload["action"] == "alert"


def test_validate_accepts_good():
    tpl.validate_body("monit_test", _GOOD)


@pytest.mark.parametrize("patch", [
    {"name": ""},                 # identity required
    {"action": "nope"},           # bad action
    {"condition": ""},            # condition required
    {"type": ""},                 # type required
])
def test_validate_rejects_bad(patch):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("monit_test", {**_GOOD, **patch})


async def test_applier_dispatches():
    calls = {}

    class FakeClient:
        async def apply_monit_test(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "operation": "add"}

    await ca.apply_for_kind(FakeClient(), "monit_test", "set", _GOOD, dry_run=True)
    assert calls["args"][0] == "set" and calls["args"][2] is True
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement introspection** — `backend/app/services/monit_introspect.py`:

```python
"""Infer a value-controlled field schema from the Monit blank test model (monit/settings/getTest).

Reuses the setting-introspection classifiers; the flat `test` model is walked once (option-objects ->
select; plain strings -> text). No exclusions (a monit test is fully fleet-portable)."""
from app.services.setting_introspect import _is_option_dict, _options, _selected


def infer_test_fields(get_test_response: dict) -> dict:
    model = (get_test_response or {}).get("test", {})
    fields: list[dict] = []
    for key, val in model.items():
        if _is_option_dict(val):
            sel = _selected(val)
            fields.append({"path": key, "label": key, "control": "select",
                           "options": _options(val), "value": sel[0] if sel else ""})
        elif isinstance(val, str):
            fields.append({"path": key, "label": key, "control": "text", "value": val})
        # lists / other -> skipped
    return {"fields": fields}
```

- [ ] **Step 4: Implement kind** — `backend/app/services/monit_kind.py`:

```python
"""Register the curated `monit_test` template kind + its config-change applier.

Body = a portable Monit health-check test {name, type, condition, action, path}. Identity = `name`;
the connector upserts by name. A test takes effect once attached to a Monit service."""
from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

_ACTIONS = {"alert", "restart", "start", "stop", "exec", "unmonitor"}


def _validate(body: dict) -> None:
    body = body or {}
    if not str(body.get("name", "")).strip():
        raise InvalidTemplateError("monit test 'name' is required (it is the test identity)")
    if not str(body.get("type", "")).strip():
        raise InvalidTemplateError("monit test 'type' is required")
    if not str(body.get("condition", "")).strip():
        raise InvalidTemplateError("monit test 'condition' is required")
    if body.get("action") not in _ACTIONS:
        raise InvalidTemplateError(f"monit test 'action' must be one of {sorted(_ACTIONS)}")


register_template_kind("monit_test", TemplateKind(
    validate=_validate,
    change_kind="monit_test",
    to_change=lambda body: ("set", str(body.get("name", "")), body),
    pinned=("name",),
))


async def _apply_monit_test(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_monit_test(operation, payload, dry_run=dry_run)


register_change_applier("monit_test", _apply_monit_test)
```

- [ ] **Step 5: Register in both processes** — in `backend/app/main.py` and `backend/app/worker.py`, add next to the `firewall_rule_kind` import:

```python
import app.services.monit_kind  # noqa: F401  — registers monit_test kind at startup
```

- [ ] **Step 6: Run → pass.**

- [ ] **Step 7: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/services/monit_introspect.py app/services/monit_kind.py app/main.py app/worker.py tests/test_monit_introspect.py tests/test_monit_kind.py
git commit -m "feat(monit): register monit_test kind + applier + introspection"
```

---

### Task 3: Read endpoint — test model

**Files:** Create `backend/app/api/monit.py`; Modify `backend/app/main.py`; Test `backend/tests/test_monit_api.py`

- [ ] **Step 1: Failing test** — mirror `tests/test_firewall_rules_api.py` (copy `_seed_members`/`_insert_device`/`_login`). Endpoint: `GET /api/tenants/{tid}/devices/{did}/opnsense/monit/test-model`. Stub `OpnsenseClient.get_monit_test_model` + `crypto.decrypt`. Assert 200 returns `{"fields":[...]}` with `type`/`action`/`name` among field paths; cross-tenant device → 404.

```python
_MODEL = {"name": "", "type": {"SystemResource": {"value": "SystemResource", "selected": 1}},
          "condition": "", "action": {"alert": {"value": "alert", "selected": 0}}, "path": ""}

async def test_test_model_returns_fields(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine); did = await _insert_device(db_engine, tid)
    async def _stub(self): return _MODEL
    monkeypatch.setattr("app.connectors.opnsense.client.OpnsenseClient.get_monit_test_model", _stub)
    monkeypatch.setattr("app.core.crypto.decrypt", lambda blob: "x")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/monit/test-model")
    assert r.status_code == 200
    paths = {f["path"] for f in r.json()["fields"]}
    assert {"name", "type", "condition", "action"} <= paths

async def test_test_model_cross_tenant_is_404(api_client, db_engine):
    tid = await _seed_members(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_tenant(s, slug="other"); await s.commit(); other_tid = other.id
    did = await _insert_device(db_engine, other_tid, name="otherfw")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/monit/test-model")
    assert r.status_code == 404
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/api/monit.py` (mirror `app/api/firewall_rules.py`):

```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.services.monit_introspect import infer_test_fields

router = APIRouter(prefix="/api", tags=["monit"])


@router.get("/tenants/{tenant_id}/devices/{device_id}/opnsense/monit/test-model")
async def monit_test_model(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Value-controlled Monit-test field schema for the template form."""
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    client = OpnsenseClient(
        device.base_url, crypto.decrypt(device.api_key_enc), crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls, tls_fingerprint=device.tls_fingerprint,
    )
    try:
        model = await client.get_monit_test_model()
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return infer_test_fields({"test": model})
```

- [ ] **Step 4: Register router** in `backend/app/main.py` (import + `app.include_router(monit_router)` after `firewall_rules_router`).

- [ ] **Step 5: Run → pass; full backend suite + ruff app/.**

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q && .venv/bin/ruff check app/
```

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/api/monit.py app/main.py tests/test_monit_api.py
git commit -m "feat(monit): GET test-model endpoint for the template form"
```

**→ Open PR 1 (backend) here, wait for green CI, merge, then continue on a fresh branch for the frontend.**

---

## FRONTEND (→ PR 2)

### Task 4: `useMonitTestModel` hook + `MonitTestForm` + i18n

**Files:** Modify `frontend/src/templates/settingHooks.ts`, `frontend/src/i18n/en.ts`; Create `frontend/src/templates/MonitTestForm.tsx`; Test `frontend/src/templates/__tests__/monitTestForm.test.tsx`.

- [ ] **Step 1: i18n** — add under `templates`: `kindMonitTest: "Monit health-check test"` and a `monit: { referenceDevice, load, loadHint, noDevice, loadFailed, nameRequired, note }` block (siblings of the other kinds). `note` ≈ "A test takes effect once attached to a Monit service."

- [ ] **Step 2: Hook** in `settingHooks.ts` (mirror `useFirewallRuleModel`, but the return type is `{ fields: SettingField[] }`):

```typescript
export function useMonitTestModel(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (): Promise<{ fields: SettingField[] }> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/opnsense/monit/test-model",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.templates.monit.loadFailed);
      return data as { fields: SettingField[] };
    },
  });
}
```

- [ ] **Step 3: Failing test** — `monitTestForm.test.tsx`, mirror `firewallRuleForm.test.tsx` harness. Body type `{ payload: Record<string,string> }`. MSW: `GET /api/tenants/t1/devices`→one device; `GET /api/tenants/t1/devices/d1/opnsense/monit/test-model`→`{fields:[{path:"name",label:"name",control:"text",value:""},{path:"action",label:"action",control:"select",options:[{value:"alert",label:"alert"},{value:"restart",label:"restart"}],value:""}]}`. Test: pick device → Load → `monit-field-name` + `monit-field-action` render; typing into name updates `latest.payload.name`.

- [ ] **Step 4: Implement** `MonitTestForm.tsx` — copy `FirewallRuleForm.tsx` and adapt: hook `useMonitTestModel`, testids `monit-device`/`monit-load`, `<AutoFormFields ... testidPrefix="monit" />`, show `monit.note`. Reuse the exported `initialPayload` from `OpnsenseSettingForm`. Controlled `{value:{payload}, onChange}`.

- [ ] **Step 5: Run → pass.**

- [ ] **Step 6: Commit**

```bash
cd frontend
git add src/templates/MonitTestForm.tsx src/templates/__tests__/monitTestForm.test.tsx src/templates/settingHooks.ts src/i18n/en.ts
git commit -m "feat(monit): MonitTestForm + useMonitTestModel hook + i18n"
```

### Task 5: Wire `monit_test` into `TemplateFormModal`

**Files:** Modify `frontend/src/templates/TemplateFormModal.tsx`; Test `frontend/src/templates/__tests__/templateFormModal.test.tsx`.

- [ ] **Step 1: Failing test** — add a `describe("TemplateFormModal — monit_test")` mirroring the firewall_rule case: switch kind to "Monit health-check test", load the model, set name + action, save, assert POST `/api/templates` body `{kind:"monit_test", name, description:"", body:{...payload incl. name, action...}}`. Add a second test: empty name → no POST (client-side guard).

- [ ] **Step 2: Implement** — add `monitBody` state (`{payload:Record<string,string>}`, EMPTY `{payload:{}}`), seed from `editing` when `editing.kind==="monit_test"`, add kind option `{value:"monit_test", label:t.templates.kindMonitTest}`, render `<MonitTestForm value={monitBody} onChange={setMonitBody} />`, add submit branch: client-side guard (`if (!String(monitBody.payload.name ?? "").trim()) { notify nameRequired; return; }`) then create `{kind:"monit_test", name, description, body: monitBody.payload}` / update. Keep all other kind branches intact.

- [ ] **Step 3: Run → pass; full frontend suite + lint + tsc.**

```bash
cd frontend && npx vitest run && npm run lint && npx tsc --noEmit
```

- [ ] **Step 4: Commit**

```bash
cd frontend
git add src/templates/TemplateFormModal.tsx src/templates/__tests__/templateFormModal.test.tsx
git commit -m "feat(monit): wire monit_test kind into the template form modal"
```

### Task 6: Regen API types, full suites, live verify

- [ ] **Step 1:** `cd frontend && npm run gen:api`; confirm `schema.d.ts` includes `monit/test-model`; `npx vitest run && npm run lint && npx tsc --noEmit` (green). Commit `chore(api): regen client types for monit test-model`.
- [ ] **Step 2: Live verify (revertible)** — ephemeral `/tmp` connector probe (creds from file, never printed): build a `monit_test` payload (`name="OPNGMSLiveTest"`, type SystemResource, condition "cpu usage is greater than 95%", action alert); `apply_monit_test("set", payload, dry_run=False)` → confirm via `searchTest`; re-apply (confirm **upsert** — still ONE); then resolve uuid + `delTest/{uuid}` + `monit/service/reconfigure`; confirm gone.
- [ ] **Step 3:** Final suites green (backend `pytest -q` + `ruff check app/`; frontend `vitest run` + `lint` + `tsc`).

---

## Self-Review notes
- No engine changes (monit tests are fully portable — no apply-time binding). Pure kind addition mirroring `firewall_rule` minus the interface.
- Upsert by `name` refuses ambiguity (never mutates on doubt); re-apply idempotent.
- Type consistency: body is the flat `{name,type,condition,action,path}` payload dict; `change_kind="monit_test"`; applier wraps as `{"test": payload}` at the HTTP boundary; consistent across connector/kind/applier/endpoint/frontend.
- `AutoFormFields` reused (testidPrefix `monit`); `infer_test_fields` reuses the setting-introspect classifiers.
