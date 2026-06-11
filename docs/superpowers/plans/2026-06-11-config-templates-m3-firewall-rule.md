# Config Templates M3 — `firewall_rule` (Rules [new]) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Ship the curated `firewall_rule` config-template kind — a portable firewall filter rule (Rules [new] / `firewall/filter` MVC API) whose target interface is chosen at apply time (empty = floating), exposing all portable rule fields via an introspection-driven, value-controlled auto-form.

**Architecture:** Extend the kind engine with a generic apply-time `bindings` channel + a per-kind `bind(body, bindings)` hook (default identity). `firewall_rule` injects the chosen `interface` at apply. The connector upserts the rule by `(description, interface)` via `firewall/filter` addRule/setRule + apply. Introspection reuses the `setting_introspect` helpers. Frontend extracts a shared `AutoFormFields` and adds an apply-time interface picker.

**Tech Stack:** Python 3.14, FastAPI async, httpx, respx, pytest. Vite + React 19 + Mantine v9 + TanStack Query v5 + openapi-fetch + Vitest/RTL/MSW.

**Verified API (real box 26.1.9, read + revertible write):** `GET firewall/filter/getRule`→`{rule:{…}}`; `POST firewall/filter/addRule {"rule":{…}}`→`{result:"saved",uuid}`; `setRule/{uuid}`; `delRule/{uuid}`; `searchRule?searchPhrase=`→`{rows:[{uuid,description,interface,…}]}`; `apply`→`{status:"OK"}`.

**Conventions:** venv `/home/l0rdg3x/coding/OPNGMS/backend/.venv/bin/python`; pytest needs `TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test`; CI lint gate is `ruff check app/` only. Mirror existing modules `app/services/ids_kind.py` (kind), `app/api/ids.py` (read endpoint), `app/services/setting_introspect.py` (introspection), `frontend/src/templates/__tests__/opnsenseSettingForm.test.tsx` (frontend test harness). English everywhere.

---

## BACKEND (→ PR 1)

### Task 1: Engine — apply-time `bindings` + per-kind `bind` hook

**Files:**
- Modify: `backend/app/services/templates.py`, `backend/app/schemas/templates.py`, `backend/app/api/templates.py`
- Test: `backend/tests/test_template_bindings.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_template_bindings.py`

```python
import pytest

from app.services import templates as tpl


def test_bind_default_is_identity():
    spec = tpl.TEMPLATE_KINDS["firewall_alias"]
    assert spec.bind is None  # alias has no bind -> identity


def test_register_kind_with_bind_and_effective_bind(monkeypatch):
    seen = {}

    def _validate(body):
        seen["validated"] = dict(body)

    spec = tpl.TemplateKind(
        validate=_validate, change_kind="x",
        to_change=lambda b: ("set", b.get("description", ""), b),
        pinned=("description",),
        bind=lambda body, b: {**body, "interface": b.get("interface", "")},
    )
    tpl.register_template_kind("_bindtest", spec)
    out = tpl.apply_bindings("_bindtest", {"description": "d"}, {"interface": "wan"})
    assert out == {"description": "d", "interface": "wan"}
    # no bindings -> floating (empty interface) via bind
    out2 = tpl.apply_bindings("_bindtest", {"description": "d"}, {})
    assert out2 == {"description": "d", "interface": ""}
    # a kind without bind returns the body unchanged
    out3 = tpl.apply_bindings("firewall_alias", {"name": "a", "type": "host", "content": ["1"]}, {"interface": "wan"})
    assert "interface" not in out3
```

- [ ] **Step 2: Run → fail** — `cd backend && .venv/bin/python -m pytest tests/test_template_bindings.py -v` (FAIL: `bind` not a field; `apply_bindings` missing).

- [ ] **Step 3: Implement** in `backend/app/services/templates.py`:

Add `bind` to the dataclass (default None) and an `apply_bindings` helper; thread bindings through `materialize_change`.

```python
from collections.abc import Callable
# ... in the dataclass:
@dataclass(frozen=True)
class TemplateKind:
    """How a template kind validates, maps to a config_change, pins identity, and binds apply-time inputs."""

    validate: Callable[[dict], None]
    change_kind: str
    to_change: Callable[[dict], tuple[str, str, dict]]
    pinned: tuple[str, ...]
    bind: Callable[[dict, dict], dict] | None = None   # (body, apply-time bindings) -> body
```

Add after `effective_body`:

```python
def apply_bindings(kind: str, body: dict, bindings: dict | None) -> dict:
    """Apply a kind's apply-time bindings (e.g. firewall interface) to the body; identity if none."""
    spec = _kind(kind)
    if spec.bind is None:
        return body
    return spec.bind(body or {}, bindings or {})
```

Change `materialize_change` to accept and apply bindings (bind BEFORE validate + to_change):

```python
async def materialize_change(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID, created_by: uuid.UUID,
    template_id: uuid.UUID, kind: str, body: dict, bindings: dict | None = None,
) -> ConfigChange:
    """Bind apply-time inputs, validate the effective body, and materialize a draft config_change."""
    spec = _kind(kind)
    body = apply_bindings(kind, body or {}, bindings)
    spec.validate(body)
    operation, target, payload = spec.to_change(body)
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind=spec.change_kind, operation=operation, target=target, payload=payload,
    )
    change.source_template_id = template_id
    await session.flush()
    return change
```

- [ ] **Step 4: Add `bindings` to the apply/preview schemas** — `backend/app/schemas/templates.py`:

```python
class ApplyTemplateIn(BaseModel):
    scheduled_at: datetime | None = None
    bindings: dict = {}


class PreviewTemplateIn(BaseModel):
    bindings: dict = {}
```

- [ ] **Step 5: Thread bindings through the API** — `backend/app/api/templates.py`:

In `apply_template`, pass `bindings=body.bindings` to `materialize_change`.
Make `preview_template` accept an optional body and apply the bindings to the effective body before deriving the change preview:

```python
from app.services.templates import (
    TEMPLATE_KINDS, InvalidTemplateError, apply_bindings, effective_body, materialize_change, validate_body,
)
from app.schemas.templates import (  # add PreviewTemplateIn to the existing import
    ApplyTemplateIn, OverrideIn, OverrideOut, PreviewTemplateIn, TemplateIn, TemplateOut,
    TemplatePreviewOut, TemplateUpdateIn,
)

@router.post(".../preview", response_model=TemplatePreviewOut, dependencies=[Depends(enforce_csrf)])
async def preview_template(
    tenant_id: uuid.UUID, device_id: uuid.UUID, template_id: uuid.UUID,
    body: PreviewTemplateIn | None = None,
    ctx: TenantContext = Depends(require_tenant(Action.CONFIG_PUSH)),
    session: AsyncSession = Depends(get_session),
) -> TemplatePreviewOut:
    await _device_or_404(session, tenant_id, device_id)
    tpl = await _template_or_404(session, template_id)
    eff = await _effective(session, tenant_id, tpl)
    eff = apply_bindings(tpl.kind, eff, (body.bindings if body else {}))
    try:
        validate_body(tpl.kind, eff)
    except InvalidTemplateError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    spec = TEMPLATE_KINDS[tpl.kind]
    op, target, _ = spec.to_change(eff)
    return TemplatePreviewOut(operation=op, kind=spec.change_kind, target=str(target), new=eff)
```

In `apply_template`, change the `materialize_change(...)` call to add `bindings=body.bindings`.

- [ ] **Step 6: Run → pass** — `cd backend && .venv/bin/python -m pytest tests/test_template_bindings.py -v`. Then the existing template/profile suites to confirm no regression: `TEST_DATABASE_URL=... .venv/bin/python -m pytest tests/test_template_kind_registry.py tests/test_setting_kind.py tests/test_ids_kind.py -q`.

- [ ] **Step 7: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/services/templates.py app/schemas/templates.py app/api/templates.py tests/test_template_bindings.py
git commit -m "feat(templates): apply-time bindings + per-kind bind hook"
```

---

### Task 2: Connector — `get_firewall_rule_model` + `apply_firewall_rule` (upsert)

**Files:** Modify `backend/app/connectors/opnsense/client.py`; Test `backend/tests/test_firewall_rule_connector.py`

- [ ] **Step 1: Failing tests** — `backend/tests/test_firewall_rule_connector.py`

```python
import httpx
import pytest
import respx

from app.connectors.opnsense.client import ApiError, OpnsenseClient


def _c():
    return OpnsenseClient("https://10.0.0.1", "k", "s", verify_tls=False)


@respx.mock
async def test_get_firewall_rule_model_returns_rule():
    respx.get(url__regex=r".*/api/firewall/filter/getRule.*").mock(
        return_value=httpx.Response(200, json={"rule": {"action": {"pass": {"value": "Pass", "selected": 1}}}}))
    model = await _c().get_firewall_rule_model()
    assert "action" in model


@respx.mock
async def test_apply_firewall_rule_add_then_apply():
    # no existing rule with this (description, interface) -> addRule
    respx.get(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": []}))
    posts = []
    def _cap(request):
        posts.append(str(request.url).split("/api/")[1])
        return httpx.Response(200, json={"result": "saved", "uuid": "u1"})
    respx.post(url__regex=r".*/api/firewall/filter/addRule.*").mock(side_effect=_cap)
    applied = respx.post(url__regex=r".*/api/firewall/filter/apply.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_firewall_rule(
        "set", {"description": "block-telnet", "interface": "wan", "action": "block"}, dry_run=False)
    assert any(p.startswith("firewall/filter/addRule") for p in posts)
    assert applied.called and res["operation"] == "add" and res["dry_run"] is False


@respx.mock
async def test_apply_firewall_rule_upsert_sets_existing():
    # exactly one existing rule with same description AND interface -> setRule/{uuid}
    respx.get(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "u9", "description": "block-telnet", "interface": "wan"},
            {"uuid": "uX", "description": "block-telnet", "interface": "lan"},  # different iface, ignored
        ]}))
    setp = respx.post(url__regex=r".*/api/firewall/filter/setRule/u9.*").mock(
        return_value=httpx.Response(200, json={"result": "saved"}))
    respx.post(url__regex=r".*/api/firewall/filter/apply.*").mock(
        return_value=httpx.Response(200, json={"status": "OK"}))
    res = await _c().apply_firewall_rule(
        "set", {"description": "block-telnet", "interface": "wan"}, dry_run=False)
    assert setp.called and res["operation"] == "set"


@respx.mock
async def test_apply_firewall_rule_ambiguous_refuses():
    respx.get(url__regex=r".*/api/firewall/filter/searchRule.*").mock(
        return_value=httpx.Response(200, json={"rows": [
            {"uuid": "a", "description": "dup", "interface": "wan"},
            {"uuid": "b", "description": "dup", "interface": "wan"},
        ]}))
    add = respx.post(url__regex=r".*/api/firewall/filter/addRule.*")
    with pytest.raises(ApiError):
        await _c().apply_firewall_rule("set", {"description": "dup", "interface": "wan"}, dry_run=False)
    assert not add.called


@respx.mock
async def test_apply_firewall_rule_dry_run_writes_nothing():
    search = respx.get(url__regex=r".*/api/firewall/filter/searchRule.*")
    add = respx.post(url__regex=r".*/api/firewall/filter/addRule.*")
    res = await _c().apply_firewall_rule("set", {"description": "d", "interface": "wan"}, dry_run=True)
    assert not search.called and not add.called and res["dry_run"] is True
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** in `client.py` (place after `apply_setting`, before `list_ids_rulesets`):

```python
    async def get_firewall_rule_model(self) -> dict:
        """Blank Rules[new] filter-rule model (option-objects/strings) for the introspection form."""
        return (await self._get("firewall/filter/getRule")).get("rule", {})

    async def apply_firewall_rule(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Upsert a Rules[new] filter rule by (description, interface), then apply.

        Verified against OPNsense 26.1.9: firewall/filter addRule/setRule/{uuid}/apply. Identity is
        (description, interface): exactly one match -> setRule; none -> addRule; many -> refuse
        (never mutate on doubt). dry_run performs NO mutation."""
        description = str(payload.get("description", ""))
        interface = str(payload.get("interface", ""))
        if dry_run:
            return {"dry_run": True, "description": description, "interface": interface}
        uuid_ = await self._resolve_rule_uuid(description, interface)
        if uuid_ is None:
            res = await self._post("firewall/filter/addRule", {"rule": payload})
            op = "add"
        else:
            res = await self._post(f"firewall/filter/setRule/{uuid_}", {"rule": payload})
            op = "set"
        await self._post("firewall/filter/apply", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": op, "result": res}

    async def _resolve_rule_uuid(self, description: str, interface: str) -> str | None:
        """Resolve an automation rule by EXACT (description, interface). None if absent; ApiError if many."""
        if not description:
            raise ApiError(0, "rule description required (it is the rule identity)")
        data = await self._post(
            "firewall/filter/searchRule", {"current": 1, "rowCount": 1000, "searchPhrase": description})
        matches = [r for r in data.get("rows", [])
                   if r.get("description") == description and str(r.get("interface", "")) == interface]
        if len(matches) > 1:
            raise ApiError(0, f"rule '{description}' on '{interface}' not uniquely resolvable ({len(matches)})")
        return matches[0]["uuid"] if matches else None
```

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/connectors/opnsense/client.py
git add app/connectors/opnsense/client.py tests/test_firewall_rule_connector.py
git commit -m "feat(fw): connector get_firewall_rule_model + upsert apply_firewall_rule"
```

---

### Task 3: Introspection — `infer_rule_fields`

**Files:** Create `backend/app/services/firewall_introspect.py`; Test `backend/tests/test_firewall_introspect.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_firewall_introspect.py`

```python
from app.services.firewall_introspect import infer_rule_fields

_MODEL = {"rule": {
    "enabled": "1",
    "action": {"pass": {"value": "Pass", "selected": 1}, "block": {"value": "Block", "selected": 0}},
    "%action": "Pass",                                   # display-mirror -> dropped
    "interface": {"wan": {"value": "WAN", "selected": 0}, "lan": {"value": "LAN", "selected": 0}},
    "gateway": {"": {"value": "none", "selected": 1}},   # device-specific -> excluded
    "source_net": "any",
    "log": "0",
    "categories": [],                                    # list -> skipped
    "description": "",
}}


def test_infer_rule_fields_excludes_device_and_mirror_fields_and_surfaces_interfaces():
    out = infer_rule_fields(_MODEL)
    paths = {f["path"] for f in out["fields"]}
    assert "action" in paths and "source_net" in paths and "log" in paths and "description" in paths
    assert "enabled" in paths
    # excluded / dropped
    for p in ("interface", "%action", "gateway", "categories", "sort_order", "prio_group"):
        assert p not in paths
    # interface options surfaced separately for the apply picker
    assert {i["value"] for i in out["interfaces"]} == {"wan", "lan"}
    # control inference
    assert next(f for f in out["fields"] if f["path"] == "action")["control"] == "select"
    assert next(f for f in out["fields"] if f["path"] == "log")["control"] == "switch"
    assert next(f for f in out["fields"] if f["path"] == "source_net")["control"] == "text"
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/services/firewall_introspect.py`:

```python
"""Infer a value-controlled field schema from the Rules[new] blank rule model (firewall/filter/getRule).

Reuses the setting-introspection classifiers. The flat `rule` model is walked once: device-specific
reference fields and computed/display-mirror fields are excluded; the `interface` field's options are
surfaced separately (they power the apply-time interface picker, not a template body field)."""
from app.services.setting_introspect import _is_option_dict, _options, _selected

# Device-specific references / computed fields that must NOT be templated (not fleet-portable).
_EXCLUDE = {
    "interface", "gateway", "replyto", "divert-to", "categories", "sched",
    "shaper1", "shaper2", "sort_order", "prio_group",
}


def infer_rule_fields(get_rule_response: dict) -> dict:
    model = (get_rule_response or {}).get("rule", {})
    fields: list[dict] = []
    interfaces: list[dict] = []
    for key, val in model.items():
        if key == "interface" and _is_option_dict(val):
            interfaces = _options(val)
            continue
        if key in _EXCLUDE or key.startswith("%"):
            continue
        if _is_option_dict(val):
            sel = _selected(val)
            multi = len(sel) >= 2
            fields.append({"path": key, "label": key,
                           "control": "multiselect" if multi else "select",
                           "options": _options(val),
                           "value": sel if multi else (sel[0] if sel else "")})
        elif isinstance(val, str) and val in ("0", "1"):
            fields.append({"path": key, "label": key, "control": "switch", "value": val})
        elif isinstance(val, str):
            fields.append({"path": key, "label": key, "control": "text", "value": val})
        # lists / other -> skipped
    return {"fields": fields, "interfaces": interfaces}
```

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit**

```bash
cd backend && .venv/bin/ruff check app/services/firewall_introspect.py
git add app/services/firewall_introspect.py tests/test_firewall_introspect.py
git commit -m "feat(fw): introspect Rules[new] model into a value-controlled field schema"
```

---

### Task 4: `firewall_rule` kind + applier + registration

**Files:** Create `backend/app/services/firewall_rule_kind.py`; Modify `backend/app/main.py`, `backend/app/worker.py`; Test `backend/tests/test_firewall_rule_kind.py`

- [ ] **Step 1: Failing test** — `backend/tests/test_firewall_rule_kind.py`

```python
import pytest

import app.services.firewall_rule_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl

_GOOD = {"description": "block-telnet", "action": "block", "direction": "in",
         "ipprotocol": "inet", "source_net": "any", "destination_net": "any",
         "destination_port": "23", "log": "1"}


def test_firewall_rule_kind_registered():
    spec = tpl.TEMPLATE_KINDS["firewall_rule"]
    assert spec.change_kind == "firewall_rule"
    op, target, payload = spec.to_change(_GOOD)
    assert op == "set" and target == "block-telnet" and payload["action"] == "block"


def test_bind_injects_interface():
    assert tpl.apply_bindings("firewall_rule", dict(_GOOD), {"interface": "wan"})["interface"] == "wan"
    assert tpl.apply_bindings("firewall_rule", dict(_GOOD), {})["interface"] == ""  # floating


def test_validate_accepts_good():
    tpl.validate_body("firewall_rule", _GOOD)


@pytest.mark.parametrize("patch", [
    {"description": ""},                       # identity required
    {"action": "allow"},                       # bad action
    {"direction": "sideways"},                 # bad direction
    {"ipprotocol": "ipx"},                     # bad ipprotocol
    {"source_net": "1.2.3.4 OR 1=1"},          # bad net (space/injection)
    {"destination_port": "ssh; rm -rf"},       # bad port
])
def test_validate_rejects_bad(patch):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("firewall_rule", {**_GOOD, **patch})


async def test_applier_dispatches():
    calls = {}

    class FakeClient:
        async def apply_firewall_rule(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "operation": "add"}

    await ca.apply_for_kind(FakeClient(), "firewall_rule", "set", _GOOD, dry_run=True)
    assert calls["args"][0] == "set" and calls["args"][2] is True
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/services/firewall_rule_kind.py`:

```python
"""Register the curated `firewall_rule` template kind (Rules[new]) + its config-change applier.

Body = a portable filter rule (all portable fields). `interface` is an apply-time binding (empty =
floating). Identity = `description`; the connector upserts by (description, interface)."""
import re

from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

_ACTIONS = {"pass", "block", "reject"}
_DIRECTIONS = {"in", "out"}
_IPPROTOCOLS = {"inet", "inet6", "inet46"}
# net: any | IP/CIDR (v4/v6) | alias name. port: empty | port/range | alias. Conservative, no spaces.
_NET_RE = re.compile(r"\A[A-Za-z0-9_.:/-]+\Z")
_PORT_RE = re.compile(r"\A[A-Za-z0-9_:-]*\Z")
_IFACE_RE = re.compile(r"\A[A-Za-z0-9_]*\Z")


def _validate(body: dict) -> None:
    body = body or {}
    if not str(body.get("description", "")).strip():
        raise InvalidTemplateError("firewall rule 'description' is required (it is the rule identity)")
    if body.get("action") not in _ACTIONS:
        raise InvalidTemplateError(f"'action' must be one of {sorted(_ACTIONS)}")
    if body.get("direction") not in _DIRECTIONS:
        raise InvalidTemplateError(f"'direction' must be one of {sorted(_DIRECTIONS)}")
    if body.get("ipprotocol") not in _IPPROTOCOLS:
        raise InvalidTemplateError(f"'ipprotocol' must be one of {sorted(_IPPROTOCOLS)}")
    for f in ("source_net", "destination_net"):
        v = str(body.get(f, "any"))
        if not _NET_RE.match(v):
            raise InvalidTemplateError(f"'{f}' must be any / an IP-CIDR / an alias name")
    for f in ("source_port", "destination_port"):
        v = str(body.get(f, ""))
        if not _PORT_RE.match(v):
            raise InvalidTemplateError(f"'{f}' must be empty / a port-range / an alias name")
    if not _IFACE_RE.match(str(body.get("interface", ""))):
        raise InvalidTemplateError("'interface' has an invalid value")


register_template_kind("firewall_rule", TemplateKind(
    validate=_validate,
    change_kind="firewall_rule",
    to_change=lambda body: ("set", str(body.get("description", "")), body),
    pinned=("description",),
    bind=lambda body, b: {**body, "interface": b.get("interface", "")},
))


async def _apply_firewall_rule(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_firewall_rule(operation, payload, dry_run=dry_run)


register_change_applier("firewall_rule", _apply_firewall_rule)
```

- [ ] **Step 4: Register in both processes** — in `backend/app/main.py` and `backend/app/worker.py`, add next to the `ids_kind` import:

```python
import app.services.firewall_rule_kind  # noqa: F401  — registers firewall_rule kind at startup
```

- [ ] **Step 5: Run → pass.**

- [ ] **Step 6: Commit**

```bash
cd backend && .venv/bin/ruff check app/
git add app/services/firewall_rule_kind.py app/main.py app/worker.py tests/test_firewall_rule_kind.py
git commit -m "feat(fw): register firewall_rule kind + applier (upsert, interface binding)"
```

---

### Task 5: Read endpoint — rule model + interfaces

**Files:** Create `backend/app/api/firewall_rules.py`; Modify `backend/app/main.py`; Test `backend/tests/test_firewall_rules_api.py`

- [ ] **Step 1: Failing test** — mirror `tests/test_ids_api.py` (seed tenant_admin, insert device, monkeypatch `OpnsenseClient.get_firewall_rule_model` + `crypto.decrypt`). Endpoint: `GET /api/tenants/{tid}/devices/{did}/opnsense/firewall/rule-model`. Assert: 200 returns `{"fields":[...], "interfaces":[...]}` with `action` among field paths and `wan` among interface values; excluded fields absent; cross-tenant device → 404.

```python
import uuid
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.factories import make_membership, make_tenant, make_user

_MODEL = {"action": {"pass": {"value": "Pass", "selected": 1}},
          "interface": {"wan": {"value": "WAN", "selected": 0}},
          "gateway": {"": {"value": "none", "selected": 1}},
          "source_net": "any", "log": "0", "description": ""}

# ... _seed_members / _insert_device / _login identical to tests/test_ids_api.py ...

async def test_rule_model_returns_fields_and_interfaces(api_client, db_engine, monkeypatch):
    tid = await _seed_members(db_engine); did = await _insert_device(db_engine, tid)
    async def _stub(self): return _MODEL
    monkeypatch.setattr("app.connectors.opnsense.client.OpnsenseClient.get_firewall_rule_model", _stub)
    monkeypatch.setattr("app.core.crypto.decrypt", lambda blob: "x")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/firewall/rule-model")
    assert r.status_code == 200
    body = r.json()
    paths = {f["path"] for f in body["fields"]}
    assert "action" in paths and "interface" not in paths and "gateway" not in paths
    assert {i["value"] for i in body["interfaces"]} == {"wan"}

async def test_rule_model_cross_tenant_is_404(api_client, db_engine):
    tid = await _seed_members(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        other = await make_tenant(s, slug="other"); await s.commit(); other_tid = other.id
    did = await _insert_device(db_engine, other_tid, name="otherfw")
    await _login(api_client, "ta@x.io")
    r = await api_client.get(f"/api/tenants/{tid}/devices/{did}/opnsense/firewall/rule-model")
    assert r.status_code == 404
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `backend/app/api/firewall_rules.py` (mirror `app/api/ids.py`):

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
from app.services.firewall_introspect import infer_rule_fields

router = APIRouter(prefix="/api", tags=["firewall-rules"])


@router.get("/tenants/{tenant_id}/devices/{device_id}/opnsense/firewall/rule-model")
async def firewall_rule_model(
    tenant_id: uuid.UUID,
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Value-controlled rule-field schema + the device's interfaces (for the apply-time picker)."""
    device = await session.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found")
    client = OpnsenseClient(
        device.base_url, crypto.decrypt(device.api_key_enc), crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls, tls_fingerprint=device.tls_fingerprint,
    )
    try:
        model = await client.get_firewall_rule_model()
    except OpnsenseError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=type(exc).__name__) from exc
    return infer_rule_fields({"rule": model})
```

- [ ] **Step 4: Register router** in `backend/app/main.py` (import + `app.include_router(firewall_rules_router)` after `ids_router`).

- [ ] **Step 5: Run → pass; then full backend suite + ruff app/.**

```bash
cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q && .venv/bin/ruff check app/
```

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/api/firewall_rules.py app/main.py tests/test_firewall_rules_api.py
git commit -m "feat(fw): GET rule-model endpoint (fields + interfaces) for the template form"
```

**→ Open PR 1 (backend) here, wait for green CI, merge, then continue on a fresh branch for the frontend.**

---

## FRONTEND (→ PR 2)

### Task 6: Extract shared `AutoFormFields` from `OpnsenseSettingForm`

**Files:** Create `frontend/src/templates/AutoFormFields.tsx`; Modify `frontend/src/templates/OpnsenseSettingForm.tsx`; Test `frontend/src/templates/__tests__/opnsenseSettingForm.test.tsx` (must stay green).

- [ ] **Step 1:** Create `AutoFormFields.tsx` — a presentational component that takes `{ fields: SettingField[], payload: Record<string,string>, onField: (path, value)=>void, testidPrefix?: string }` and renders the switch/select/multiselect/text controls (lift the exact JSX block from `OpnsenseSettingForm` lines that map `fields`). Default `testidPrefix="setting"` so existing `setting-field-*` testids are unchanged.

- [ ] **Step 2:** Refactor `OpnsenseSettingForm` to render `<AutoFormFields fields={fields} payload={value.payload} onField={setField} />` in place of the inline `fields.map(...)`. Keep all existing testids/behavior.

- [ ] **Step 3:** Run the setting form tests — they MUST stay green unchanged:
`cd frontend && npx vitest run src/templates/__tests__/opnsenseSettingForm.test.tsx`

- [ ] **Step 4: Commit**

```bash
cd frontend
git add src/templates/AutoFormFields.tsx src/templates/OpnsenseSettingForm.tsx
git commit -m "refactor(templates): extract AutoFormFields shared field renderer"
```

### Task 7: `useFirewallRuleModel` hook + `FirewallRuleForm` + i18n

**Files:** Modify `frontend/src/templates/settingHooks.ts` (add hook + `RuleModel` type), `frontend/src/i18n/en.ts`; Create `frontend/src/templates/FirewallRuleForm.tsx`; Test `frontend/src/templates/__tests__/firewallRuleForm.test.tsx`.

- [ ] **Step 1:** i18n — add under `templates`: `kindFirewallRule: "Firewall rule (Rules [new])"` and a `fw: { referenceDevice, load, loadHint, noDevice, loadFailed, descriptionRequired, interface, floating, note }` block.

- [ ] **Step 2:** Hook in `settingHooks.ts`:

```typescript
export type RuleModel = { fields: SettingField[]; interfaces: { value: string; label: string }[] };

export function useFirewallRuleModel(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (): Promise<RuleModel> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/opnsense/firewall/rule-model",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.templates.fw.loadFailed);
      return data as RuleModel;
    },
  });
}
```

- [ ] **Step 3:** Failing test — `firewallRuleForm.test.tsx`, mirror `opnsenseSettingForm.test.tsx` harness (shared `server`, `makeWrapper` w/ TenantContext activeId "t1", `latest` capture). Body type `{ payload: Record<string,string> }`. MSW: `GET /api/tenants/t1/devices` → `[{id:"d1",name:"fw1", ...full shape}]`; `GET /api/tenants/t1/devices/d1/opnsense/firewall/rule-model` → `{fields:[{path:"action",label:"action",control:"select",options:[{value:"pass",label:"Pass"},{value:"block",label:"Block"}],value:"pass"},{path:"description",label:"description",control:"text",value:""}], interfaces:[{value:"wan",label:"WAN"}]}`. Test: pick device → Load → the auto-form renders `setting-field-action` (AutoFormFields default prefix) + a `fw-description`-ish field; changing description updates `latest.payload.description`.

- [ ] **Step 4:** Implement `FirewallRuleForm.tsx` — controlled `{value:{payload}, onChange}`. Reference-device Select (`fw-device`) → Load button (`fw-load`) → on success `setFields(res.fields)` + seed payload defaults merged over saved (`{...initialPayload(fields), ...value.payload}`, reuse the `initialPayload` helper — export it from `OpnsenseSettingForm` or duplicate the 5-line helper) → `<AutoFormFields fields payload onField />`. Show `fw.note`. (No endpoint select; the "endpoint" is the fixed rule model.)

- [ ] **Step 5:** Run → pass.

- [ ] **Step 6: Commit**

```bash
cd frontend
git add src/templates/FirewallRuleForm.tsx src/templates/__tests__/firewallRuleForm.test.tsx src/templates/settingHooks.ts src/i18n/en.ts
git commit -m "feat(fw): FirewallRuleForm + useFirewallRuleModel hook + i18n"
```

### Task 8: Wire `firewall_rule` into `TemplateFormModal`

**Files:** Modify `frontend/src/templates/TemplateFormModal.tsx`; Test `frontend/src/templates/__tests__/templateFormModal.test.tsx`.

- [ ] **Step 1:** Failing test — add a case rendering the modal directly (as the IDS test does), switching kind to "Firewall rule (Rules [new])", loading the model, setting a description, saving, asserting POST body `{kind:"firewall_rule", name, description:"", body:{...payload incl. description...}}`.

- [ ] **Step 2:** Implement — add `ruleBody` state (`{payload:Record<string,string>}`, EMPTY `{payload:{}}`), seed from `editing` when `editing.kind==="firewall_rule"`, add kind option, render `<FirewallRuleForm value={ruleBody} onChange={setRuleBody} />`, add submit branch (create `{kind:"firewall_rule", name, description, body: ruleBody.payload}` — NB the body sent is the payload dict itself, matching the backend body shape). Keep alias/opnsense_setting/suricata_ruleset branches intact.

- [ ] **Step 3:** Run → pass.

- [ ] **Step 4: Commit**

```bash
cd frontend
git add src/templates/TemplateFormModal.tsx src/templates/__tests__/templateFormModal.test.tsx
git commit -m "feat(fw): wire firewall_rule kind into the template form modal"
```

### Task 9: Apply flow — interface picker + bindings

**Files:** Modify the per-device apply component (`frontend/src/templates/ApplyTemplate*.tsx` — locate via `grep -rl "preview" src/templates`); add the `bindings` field to the preview/apply calls; Test the relevant apply test (`src/templates/__tests__/applyTemplate.test.tsx`).

- [ ] **Step 1:** Inspect the existing apply component + its hooks (`src/templates/hooks.ts` preview/apply mutations). Determine where the selected template + its kind are known at apply time.

- [ ] **Step 2:** Failing test — in `applyTemplate.test.tsx`, when the selected template's kind is `firewall_rule`, an interface Select (`fw-apply-interface`, options from the device `rule-model.interfaces` + an empty "floating" entry) is shown, and clicking Preview/Apply sends `bindings:{interface:<chosen>}` in the request body. Mock `GET .../firewall/rule-model` for the interface list and capture the preview/apply POST bodies.

- [ ] **Step 3:** Implement — extend the preview/apply hooks to accept an optional `bindings` and include it in the POST body; in the apply component, when `template.kind === "firewall_rule"`, load the device's interfaces (via `useFirewallRuleModel`) and render the interface Select (empty = floating), threading `{interface}` as bindings into preview + apply. Non-firewall kinds send no bindings (unchanged).

- [ ] **Step 4:** Run → pass; then full frontend suite + lint + tsc.

```bash
cd frontend && npx vitest run && npm run lint && npx tsc --noEmit
```

- [ ] **Step 5: Commit**

```bash
cd frontend
git add src/templates/hooks.ts src/templates/ApplyTemplate*.tsx src/templates/__tests__/applyTemplate.test.tsx
git commit -m "feat(fw): apply-time interface picker + bindings (empty = floating)"
```

### Task 10: Regen API types, full suites, live verify

- [ ] **Step 1:** `cd frontend && npm run gen:api` (backend must be importable). Confirm `schema.d.ts` includes `firewall/rule-model` and the `bindings` field on apply/preview. Then `npx vitest run && npm run lint && npx tsc --noEmit` (all green). Commit `chore(api): regen client types for firewall rule-model + bindings`.

- [ ] **Step 2: Live verify (revertible)** — via an ephemeral `/tmp` connector probe (`verify_tls=False`, creds from the file, never printed): build a `firewall_rule` payload (a **disabled** block rule, `description="OPNGMS-LIVE-TEST"`), call `apply_firewall_rule("set", payload+{"interface":"wan"}, dry_run=False)` → confirm it appears in `searchRule`; call again (confirm **upsert** — still exactly ONE rule, not two); then revert: resolve its uuid and `delRule/{uuid}` + `apply`; confirm gone. Document the result in the PR.

- [ ] **Step 3:** Final suites green (backend `pytest -q` + `ruff check app/`; frontend `vitest run` + `lint` + `tsc`).

---

## Self-Review notes
- **Engine bindings** are generic (Task 1); only `firewall_rule` uses `bind` today; alias/setting/ids are unchanged (bind defaults None → identity). Profiles pass no bindings → firewall_rule members apply floating (documented M3 limit).
- **Path/identity safety:** interface/net/port are charset-validated in the kind; the connector upsert resolves by EXACT (description, interface) and refuses ambiguity (never mutates on doubt). Re-apply is idempotent (no duplicate rules).
- **Type consistency:** template body for firewall_rule is the flat `payload` dict (`{field: value}`); `change_kind="firewall_rule"`; applier signature `(client, operation, payload, *, dry_run)`; the rule POST wraps it as `{"rule": payload}`. Consistent across connector, kind, applier, endpoint, and frontend.
- **Frontend DRY:** `AutoFormFields` is shared by the setting form and the rule form; the setting tests must remain green unchanged after the extract.
