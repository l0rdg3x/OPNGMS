# Configuration Templates — M3 generic `opnsense_setting` kind — Backend Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Backend for the generic, introspection-driven `opnsense_setting` template kind: a curated catalog of fleet-portable OPNsense setting endpoints; a connector that reads a setting (`get`, for the UI's auto-form) and applies a **partial** setting (`set` + `reconfigure`, verified on real 26.1.9); a field-inference service that turns a `get` response into a value-controlled field schema (skipping hardware-specific fields); and the kind registered on the M3a engine. Proven end-to-end on **IDS general settings**.

**Architecture:** No new tables (the kind rides on `config_templates`, `body` JSONB = `{endpoint_key, payload}`). A `SETTING_ENDPOINTS` catalog (data); `OpnsenseClient.get_setting`/`apply_setting`; `services/setting_introspect.infer_fields`; `register_template_kind("opnsense_setting", ...)` + `register_change_applier("opnsense_setting", ...)`; two API endpoints (catalog list + per-device introspection). Apply reuses the config-push pipeline.

**Spec:** `docs/superpowers/specs/2026-06-11-config-templates-m3-generic-setting-design.md`
**Branch:** `feat/templates-m3-generic-setting` (created; spec committed).
**Verified on real 26.1.9:** `GET ids/settings/get` returns the model with option-objects/`0|1`/strings; **partial** `POST ids/settings/set {"ids":{"general":{"<field>":<val>}}}` MERGES (saved, no clobber, untouched fields not validated); `POST ids/service/reconfigure` → `{"status":"OK"}`; option fields set by comma-joined selected keys.
**Reuse (M3a, merged):** `services/templates.py` (`TemplateKind`, `register_template_kind`, `InvalidTemplateError`), `services/config_apply.py` (`register_change_applier`), the config-push pipeline.
**Scope:** Backend. Frontend (the auto-form) is a separate plan. The curated kinds (IDS-rulesets, rules, monit) are later milestones.

**Run tests:** `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest <files> -q`. English; ruff-clean; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: Endpoint catalog + connector (get/apply setting)

**Files:** Create `backend/app/connectors/opnsense/setting_endpoints.py`, `backend/tests/test_setting_connector.py`; Modify `backend/app/connectors/opnsense/client.py`.

- [ ] **Step 1: Create the catalog** `backend/app/connectors/opnsense/setting_endpoints.py`:
```python
"""Curated catalog of fleet-portable OPNsense model-setting endpoints that may be templated.

Only portable settings are listed (no inherently hardware/device-specific endpoints), and each
entry declares `exclude_fields` for any per-device fields to omit from the form. Adding an endpoint
is a data-only change."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class SettingEndpoint:
    key: str
    label: str
    get_path: str
    set_path: str
    reconfigure_path: str
    model_root: str
    multi_fields: tuple[str, ...] = ()       # dotted paths that are multi-select option fields
    exclude_fields: tuple[str, ...] = ()      # dotted paths to OMIT (hardware/device-specific)


SETTING_ENDPOINTS: dict[str, SettingEndpoint] = {
    "ids_general": SettingEndpoint(
        key="ids_general", label="IDS — General settings",
        get_path="ids/settings/get", set_path="ids/settings/set",
        reconfigure_path="ids/service/reconfigure", model_root="ids",
        multi_fields=("general.homenet",),
        exclude_fields=("general.interfaces",),   # per-device hardware — not templatable
    ),
}
```

- [ ] **Step 2: Write `backend/tests/test_setting_connector.py`** (respx):
```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_setting():
    respx.get(url__regex=r".*/api/ids/settings/get.*").mock(
        return_value=httpx.Response(200, json={"ids": {"general": {"enabled": "0"}}}))
    out = await _c().get_setting("ids/settings/get")
    assert out["ids"]["general"]["enabled"] == "0"


@respx.mock
async def test_apply_setting_partial_then_reconfigure():
    captured = {}
    def _cap(request):
        import json
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"result": "saved"})
    respx.post(url__regex=r".*/api/ids/settings/set.*").mock(side_effect=_cap)
    rec = respx.post(url__regex=r".*/api/ids/service/reconfigure.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_setting(
        "ids/settings/set", "ids/service/reconfigure", "ids",
        {"general.enabled": "1", "general.homenet": "a,b"}, dry_run=False)
    # un-flattened under the model root; partial (only the templated fields)
    assert captured == {"ids": {"general": {"enabled": "1", "homenet": "a,b"}}}
    assert rec.called and res["dry_run"] is False


@respx.mock
async def test_apply_setting_dry_run_writes_nothing():
    s = respx.post(url__regex=r".*/api/ids/settings/set.*")
    res = await _c().apply_setting("ids/settings/set", "ids/service/reconfigure", "ids",
                                   {"general.enabled": "1"}, dry_run=True)
    assert not s.called and res["dry_run"] is True
```

- [ ] **Step 3: Run → FAIL** (no `get_setting`/`apply_setting`).

- [ ] **Step 4: Add to `client.py`** (near `apply_alias`). A module-level un-flatten helper + the two methods:
```python
    async def get_setting(self, get_path: str) -> dict:
        """Read an OPNsense model-setting endpoint (for introspection)."""
        return await self._get(get_path)

    async def apply_setting(self, set_path: str, reconfigure_path: str, model_root: str,
                            payload: dict, *, dry_run: bool = True) -> dict:
        """Apply a PARTIAL setting: POST only the templated fields under the model root, then
        reconfigure. Verified: OPNsense `set` merges a partial payload (no clobber). Payload keys are
        dotted paths (e.g. 'general.homenet'); values are strings (option fields = comma-joined keys)."""
        if dry_run:
            return {"dry_run": True, "endpoint": set_path, "fields": sorted(payload.keys())}
        nested = _unflatten(payload)
        res = await self._post(set_path, {model_root: nested})
        await self._post(reconfigure_path, {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}
```
And add the module-level helper (near `_PLUGIN_NAME_RE`):
```python
def _unflatten(flat: dict) -> dict:
    """{'a.b': 1, 'a.c': 2, 'x': 3} -> {'a': {'b': 1, 'c': 2}, 'x': 3}."""
    out: dict = {}
    for key, val in flat.items():
        parts = key.split(".")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return out
```
(Confirm `_post`'s signature `_post(path, json, timeout=None)` and `RECONFIGURE_TIMEOUT` exist — they do.)

- [ ] **Step 5: Run → PASS.** Also run `tests/test_connector_apply_alias.py` (no regression). Commit:
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/connectors/opnsense/setting_endpoints.py backend/app/connectors/opnsense/client.py backend/tests/test_setting_connector.py
git commit -m "feat(opnsense): setting endpoint catalog + get_setting/apply_setting (partial set + reconfigure)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Field-inference service

**Files:** Create `backend/app/services/setting_introspect.py`, `backend/tests/test_setting_introspect.py`.

**Context:** Turn a `get` response into a value-controlled field schema. Walk `response[model_root]` recursively; SKIP `exclude_fields`; classify each leaf: option-dict (values are `{value, selected}`) → `select` (or `multiselect` if ≥2 selected OR path in `multi_fields`); `"0"|"1"` → `switch`; plain string → `text`; nested object → recurse (dotted path). Non-dict/str leaves (lists) are SKIPPED (advanced, not templatable here).

- [ ] **Step 1: Write `backend/tests/test_setting_introspect.py`:**
```python
from app.connectors.opnsense.setting_endpoints import SettingEndpoint
from app.services.setting_introspect import infer_fields

EP = SettingEndpoint(
    key="t", label="T", get_path="m/c/get", set_path="m/c/set", reconfigure_path="m/s/reconfigure",
    model_root="m", multi_fields=("g.multi",), exclude_fields=("g.hw",))


def _schema_for(model):
    return {f["path"]: f for f in infer_fields({"m": model}, EP)}


def test_infers_controls_and_skips_excluded_and_lists():
    model = {"g": {
        "enabled": "0",                                                  # switch
        "mode": {"a": {"value": "A", "selected": 1}, "b": {"value": "B", "selected": 0}},  # select
        "multi": {"x": {"value": "X", "selected": 1}, "y": {"value": "Y", "selected": 0}}, # multiselect (hint)
        "many": {"p": {"value": "P", "selected": 1}, "q": {"value": "Q", "selected": 1}},  # multiselect (>=2)
        "name": "hello",                                                 # text
        "hw": {"wan": {"value": "WAN", "selected": 1}},                  # EXCLUDED
        "rules": [1, 2, 3],                                              # list -> skipped
    }}
    s = _schema_for(model)
    assert s["g.enabled"]["control"] == "switch" and s["g.enabled"]["value"] == "0"
    assert s["g.mode"]["control"] == "select" and s["g.mode"]["value"] == "a"
    assert {o["value"] for o in s["g.mode"]["options"]} == {"a", "b"}
    assert s["g.multi"]["control"] == "multiselect" and s["g.multi"]["value"] == ["x"]
    assert s["g.many"]["control"] == "multiselect" and set(s["g.many"]["value"]) == {"p", "q"}
    assert s["g.name"]["control"] == "text" and s["g.name"]["value"] == "hello"
    assert "g.hw" not in s          # excluded
    assert "g.rules" not in s       # list skipped
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `backend/app/services/setting_introspect.py`:**
```python
"""Infer a value-controlled field schema from an OPNsense model `get` response.

Heuristic (precise for the common shapes; OPNsense's own `set` validation is the final backstop):
option-dict -> select / multiselect; "0"|"1" -> switch; plain string -> text; nested object ->
recurse (dotted path). Fields in the endpoint's `exclude_fields` (hardware/device-specific) and
non-dict/str leaves (lists) are skipped."""
from app.connectors.opnsense.setting_endpoints import SettingEndpoint


def _is_option_dict(v) -> bool:
    return isinstance(v, dict) and len(v) > 0 and all(
        isinstance(o, dict) and "selected" in o for o in v.values())


def _options(v: dict) -> list[dict]:
    return [{"value": k, "label": str(o.get("value", k))} for k, o in v.items()]


def _selected(v: dict) -> list[str]:
    return [k for k, o in v.items() if str(o.get("selected")) == "1"]


def infer_fields(get_response: dict, endpoint: SettingEndpoint) -> list[dict]:
    model = (get_response or {}).get(endpoint.model_root, {})
    out: list[dict] = []
    _walk(model, "", endpoint, out)
    return out


def _walk(node: dict, prefix: str, ep: SettingEndpoint, out: list[dict]) -> None:
    for key, val in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if path in ep.exclude_fields:
            continue
        if _is_option_dict(val):
            sel = _selected(val)
            multi = len(sel) >= 2 or path in ep.multi_fields
            out.append({"path": path, "label": key,
                        "control": "multiselect" if multi else "select",
                        "options": _options(val),
                        "value": sel if multi else (sel[0] if sel else "")})
        elif isinstance(val, str) and val in ("0", "1"):
            out.append({"path": path, "label": key, "control": "switch", "value": val})
        elif isinstance(val, str):
            out.append({"path": path, "label": key, "control": "text", "value": val})
        elif isinstance(val, dict):
            _walk(val, path, ep, out)   # nested object -> recurse
        # else (list / other) -> skipped
```

- [ ] **Step 4: Run → PASS. Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/setting_introspect.py backend/tests/test_setting_introspect.py
git commit -m "feat(templates): setting field-inference (value-controlled schema, skips hardware/lists)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Register the `opnsense_setting` kind

**Files:** Create `backend/app/services/setting_kind.py`, `backend/tests/test_setting_kind.py`; Modify `backend/app/services/templates.py` (import the registration) OR ensure it's imported at startup.

**Context:** Register the template kind + the change applier. The template body = `{endpoint_key, payload}`. The applier looks up the catalog, calls `apply_setting`.

- [ ] **Step 1: Write `backend/tests/test_setting_kind.py`:**
```python
import pytest

from app.services import templates as tpl
from app.services import config_apply as ca
import app.services.setting_kind  # noqa: F401  (registers on import)


def test_opnsense_setting_kind_registered():
    spec = tpl.TEMPLATE_KINDS["opnsense_setting"]
    assert spec.change_kind == "opnsense_setting"
    op, target, payload = spec.to_change({"endpoint_key": "ids_general", "payload": {"general.enabled": "1"}})
    assert op == "set" and target == "ids_general" and payload["endpoint_key"] == "ids_general"


def test_validate_rejects_unknown_endpoint():
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("opnsense_setting", {"endpoint_key": "nope", "payload": {}})


async def test_applier_dispatches_to_apply_setting():
    calls = {}

    class FakeClient:
        async def apply_setting(self, set_path, reconfigure_path, model_root, payload, *, dry_run):
            calls["args"] = (set_path, reconfigure_path, model_root, payload, dry_run)
            return {"dry_run": dry_run, "result": "ok"}

    res = await ca.apply_for_kind(
        FakeClient(), "opnsense_setting", "set",
        {"endpoint_key": "ids_general", "payload": {"general.enabled": "1"}}, dry_run=True)
    set_path, rec_path, root, payload, dry = calls["args"]
    assert set_path == "ids/settings/set" and rec_path == "ids/service/reconfigure" and root == "ids"
    assert payload == {"general.enabled": "1"} and dry is True
```

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement `backend/app/services/setting_kind.py`:**
```python
"""Register the generic `opnsense_setting` template kind + its config-change applier."""
from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS
from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind


def _validate(body: dict) -> None:
    body = body or {}
    if body.get("endpoint_key") not in SETTING_ENDPOINTS:
        raise InvalidTemplateError(f"unknown setting endpoint: {body.get('endpoint_key')!r}")
    if not isinstance(body.get("payload"), dict):
        raise InvalidTemplateError("setting 'payload' must be an object")


register_template_kind("opnsense_setting", TemplateKind(
    validate=_validate,
    change_kind="opnsense_setting",
    to_change=lambda body: ("set", body["endpoint_key"], body),   # payload = the whole body
    pinned=("endpoint_key",),                                      # override may tweak payload, not repoint
))


async def _apply_opnsense_setting(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    ep = SETTING_ENDPOINTS.get(payload.get("endpoint_key"))
    if ep is None:
        raise InvalidTemplateError(f"unknown setting endpoint: {payload.get('endpoint_key')!r}")
    return await client.apply_setting(
        ep.set_path, ep.reconfigure_path, ep.model_root, payload.get("payload", {}), dry_run=dry_run)


register_change_applier("opnsense_setting", _apply_opnsense_setting)
```

- [ ] **Step 4: Ensure the kind is registered at app startup** — the registration runs on `import app.services.setting_kind`. Add that import where the app wires services so it runs in production (e.g. in `app/main.py` alongside the routers, or in `app/services/__init__.py`). READ `app/main.py`: if it imports routers that transitively import `setting_kind`, fine; otherwise add `import app.services.setting_kind  # noqa: F401` near the top of `main.py` (or in the API module that handles templates). Confirm `materialize_change`/`apply_for_kind` see the registered kind at runtime (the API test in Task 4 will prove it end-to-end).

- [ ] **Step 5: Run → PASS. Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/setting_kind.py backend/tests/test_setting_kind.py backend/app/main.py
git commit -m "feat(templates): register opnsense_setting kind (validate + applier -> apply_setting)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(Drop `main.py` if you registered the import elsewhere.)

---

## Task 4: API (catalog + introspection)

**Files:** Create `backend/app/api/settings.py`, `backend/tests/test_settings_api.py`; Modify `backend/app/main.py`.

**Context:** Two endpoints: the catalog (any-auth, for the kind picker) and per-device introspection (tenant-scoped, reads the device's `get` → returns the inferred field schema). READ `app/api/templates.py` for the deps.

- [ ] **Step 1: Create `backend/app/api/settings.py`:**
```python
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.opnsense.client import OpnsenseClient, OpnsenseError
from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS
from app.core import crypto
from app.core.db import get_session
from app.core.deps import TenantContext, get_current_user, require_tenant
from app.core.rbac import Action
from app.models.device import Device
from app.models.user import User
from app.services.setting_introspect import infer_fields

router = APIRouter(prefix="/api", tags=["settings"])


@router.get("/opnsense/setting-endpoints")
async def list_setting_endpoints(user: User = Depends(get_current_user)) -> list[dict]:
    return [{"key": e.key, "label": e.label} for e in SETTING_ENDPOINTS.values()]


@router.get("/tenants/{tenant_id}/devices/{device_id}/opnsense/settings/{endpoint_key}")
async def introspect_setting(
    tenant_id: uuid.UUID, device_id: uuid.UUID, endpoint_key: str,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    ep = SETTING_ENDPOINTS.get(endpoint_key)
    if ep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown setting endpoint")
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    client = OpnsenseClient(device.base_url, crypto.decrypt(device.api_key_enc),
                            crypto.decrypt(device.api_secret_enc), verify_tls=device.verify_tls,
                            tls_fingerprint=device.tls_fingerprint)
    try:
        raw = await client.get_setting(ep.get_path)
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return {"endpoint_key": ep.key, "label": ep.label, "fields": infer_fields(raw, ep)}
```
Confirm the dep imports against `app/api/templates.py`. Wire the router in `main.py` (mirror the templates include).

- [ ] **Step 2: Write `backend/tests/test_settings_api.py`** — mirror `test_templates_api.py` helpers. Tests: `GET /api/opnsense/setting-endpoints` (auth) lists `ids_general`; `GET .../devices/{id}/opnsense/settings/ids_general` with a respx-mocked device `get` returns a `fields` schema (and the `general.interfaces` field is ABSENT — excluded); unknown endpoint → 404; cross-tenant device → 404. (Mock the device `get` via respx against the device base_url, like other connector-touching API tests; if no such pattern exists, mock at the `OpnsenseClient.get_setting` level via monkeypatch.)

- [ ] **Step 3: Run → FAIL → implement is already done in Step 1 → wire + run → PASS.** Also run `tests/test_templates_api.py` (no regression).

- [ ] **Step 4: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/api/settings.py backend/app/main.py backend/tests/test_settings_api.py
git commit -m "feat(templates): setting catalog + per-device introspection API

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Live verify script

**Files:** Create `scripts/verify_setting_live.py`.

**Context:** Prove the introspect→apply round-trip on the real box. Introspect `ids/settings/get`, flip ONE benign portable field (e.g. `general.AlertSaveLogs`), apply via `apply_setting` (partial set + reconfigure), confirm via re-introspect, then revert (guaranteed cleanup). Never enable the IDS engine; never print creds. Mirror `verify_template_live.py`'s structure.

- [ ] **Step 1: Create `scripts/verify_setting_live.py`** — read creds; `OpnsenseClient(verify_tls=False)`; `ep = SETTING_ENDPOINTS["ids_general"]`; introspect (`get_setting` + `infer_fields`) to read the current `AlertSaveLogs`; pick a different benign value; `apply_setting(set,reconfigure,model_root,{"general.AlertSaveLogs": new}, dry_run=False)`; re-read to confirm; in a `finally`, `apply_setting(... {"general.AlertSaveLogs": original})` to revert + confirm reverted. Print per-step lines + `ALL PASS`/`FAILED`.
- [ ] **Step 2: Parse + import check.** Commit:
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add scripts/verify_setting_live.py
git commit -m "tools(templates): live opnsense_setting introspect+apply verify (revertible)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
> **Orchestrator note:** runs it against the real box (flip a benign IDS-general field + revert), proving the generic introspect→partial-set→reconfigure round-trip on real hardware.

---

## Final verification

- [ ] Full backend suite green + ruff clean.
- [ ] Live setting verify (orchestrator) → `ALL PASS`, box reverted/clean.
- [ ] Final holistic review (focus: catalog is an allowlist / no arbitrary-path writes; partial-set no-clobber; hardware fields excluded; the kind plugs into M3a cleanly), then finishing-a-development-branch → PR. Frontend (the auto-form) is a separate plan.

---

## Self-Review (author)

**Spec coverage (backend):** the curated catalog (Task 1); the connector get/apply-setting with verified partial-set + un-flatten (Task 1); the value-controlled field inference skipping hardware/lists (Task 2); the `opnsense_setting` kind on the M3a registries (Task 3); the catalog + introspection API (Task 4); live proof on IDS-general (Task 5). No new tables; rides on `config_templates` + the config-push pipeline. Hardware/device-specific fields excluded (catalog `exclude_fields`). Curated kinds (IDS-rulesets/rules/monit) are later milestones.

**Placeholder scan:** Tasks 1-3 carry complete code; Task 4's API is complete + the test step names the file to mirror; Task 5 names `verify_template_live.py` as the mirror. The startup-registration step (Task 3 Step 4) gives a concrete instruction + a runtime-proof via the Task 4 API test.

**Type consistency:** `apply_setting(set_path, reconfigure_path, model_root, payload, *, dry_run)` matches the kind applier (Task 3) + the connector (Task 1) + the live script (Task 5); `infer_fields(get_response, endpoint)` matches the API (Task 4) + the live script; `SettingEndpoint`/`SETTING_ENDPOINTS` shared across catalog/inference/kind/API; the kind registers on M3a's `register_template_kind`/`register_change_applier`.
