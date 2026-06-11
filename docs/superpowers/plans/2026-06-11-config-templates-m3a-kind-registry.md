# Configuration Templates — M3a: kind-pluggable engine — Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Make the template engine + the config-push apply path **kind-pluggable** — replace the two hardcoded `firewall_alias`/`alias` dispatch points with registries — so new template kinds (M3b Suricata/IDS, M3c firewall rules, M3d monit) register an entry instead of editing core code. The existing `firewall_alias`→`alias` path keeps working unchanged.

**Architecture:** Two registries. (1) **Template-kind registry** in `services/templates.py`: `template_kind → {validate, change_kind, to_change(body), pinned}`. `materialize_change`/`validate_body`/`effective_body` use it. (2) **Change-kind apply registry** in a new `services/config_apply.py`: `change_kind → async applier(client, operation, payload, *, dry_run)`; `config_push.apply_change` dispatches through it. Each registry exposes a `register_*` function (and is seeded with the `firewall_alias`/`alias` entry). No behavior change for existing kinds.

**Tech Stack:** Python 3.14, pytest. **Branch:** `feat/templates-m3a-kind-registry`. **Scope:** backend refactor only; no new tables, no frontend, no new connector write (those come with the concrete kinds). **Spec context:** `docs/superpowers/specs/2026-06-11-config-templates-m1-design.md` + the M3 design discussion (kind-pluggable enabler).

**Run tests:** `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest <files> -q`. English; trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## Task 1: Template-kind registry (`services/templates.py`)

**Files:** Modify `backend/app/services/templates.py`; Create `backend/tests/test_template_kind_registry.py`.

**Context:** Today `services/templates.py` has `_VALIDATORS = {"firewall_alias": ...}`, a global `_PINNED = ("name","type")`, and `materialize_change` hardcodes `if kind != "firewall_alias": raise` + maps to `create_change(kind="alias", operation="set", target=body["name"], payload=body)`. Replace with a registry so each kind declares its validator, the config_change `kind` it maps to, how its body becomes `(operation, target, payload)`, and its pinned identity fields. Keep `firewall_alias` behavior IDENTICAL (alias / set / target=name / payload=body / pinned name,type).

- [ ] **Step 1: Write `backend/tests/test_template_kind_registry.py`:**
```python
import uuid

import pytest

from app.services import templates as tpl


def test_firewall_alias_is_registered_and_maps_to_alias():
    spec = tpl.TEMPLATE_KINDS["firewall_alias"]
    assert spec.change_kind == "alias"
    op, target, payload = spec.to_change({"name": "web", "type": "host", "content": ["1.2.3.4"]})
    assert op == "set" and target == "web" and payload["name"] == "web"
    assert spec.pinned == ("name", "type")


def test_validate_body_unknown_kind_raises():
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("nope", {})


def test_effective_body_uses_per_kind_pinned():
    # firewall_alias pins name+type; a patch cannot change them
    base = {"name": "web", "type": "host", "content": ["1.1.1.1"], "description": "b"}
    eff = tpl.effective_body("firewall_alias", base, {"name": "X", "type": "url", "content": ["2.2.2.2"]})
    assert eff["name"] == "web" and eff["type"] == "host" and eff["content"] == ["2.2.2.2"]


def test_register_and_materialize_a_custom_kind(monkeypatch):
    # Register a throwaway kind to prove extensibility, then clean up.
    def _validate(body):
        if not body.get("svc"):
            raise tpl.InvalidTemplateError("svc required")

    spec = tpl.TemplateKind(
        validate=_validate, change_kind="custom_demo",
        to_change=lambda body: ("set", body["svc"], body), pinned=("svc",),
    )
    tpl.register_template_kind("demo_kind", spec)
    try:
        assert "demo_kind" in tpl.TEMPLATE_KINDS
        tpl.validate_body("demo_kind", {"svc": "x"})
        with pytest.raises(tpl.InvalidTemplateError):
            tpl.validate_body("demo_kind", {})
        # effective_body pins "svc"
        eff = tpl.effective_body("demo_kind", {"svc": "a", "v": 1}, {"svc": "HACK", "v": 2})
        assert eff["svc"] == "a" and eff["v"] == 2
    finally:
        tpl.TEMPLATE_KINDS.pop("demo_kind", None)
```

- [ ] **Step 2: Run → FAIL** (`AttributeError: TEMPLATE_KINDS`).

- [ ] **Step 3: Refactor `services/templates.py`** — keep `InvalidTemplateError`, `ALIAS_TYPES`, `validate_alias_body` as-is. Replace `_VALIDATORS`/`_PINNED`/the materialize branch with the registry:
```python
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class TemplateKind:
    """How a template kind validates, maps to a config_change, and pins identity fields."""
    validate: Callable[[dict], None]
    change_kind: str                                   # the config_change.kind it materializes to
    to_change: Callable[[dict], tuple[str, str, dict]]  # body -> (operation, target, payload)
    pinned: tuple[str, ...]                              # body keys an override may not change


TEMPLATE_KINDS: dict[str, TemplateKind] = {}


def register_template_kind(kind: str, spec: TemplateKind) -> None:
    TEMPLATE_KINDS[kind] = spec


# --- firewall_alias (M1) ---
register_template_kind("firewall_alias", TemplateKind(
    validate=validate_alias_body,
    change_kind="alias",
    to_change=lambda body: ("set", body["name"], body),
    pinned=("name", "type"),
))


def _kind(kind: str) -> TemplateKind:
    spec = TEMPLATE_KINDS.get(kind)
    if spec is None:
        raise InvalidTemplateError(f"unsupported template kind: {kind}")
    return spec


def validate_body(kind: str, body: dict) -> None:
    _kind(kind).validate(body or {})


def effective_body(kind: str, base: dict, patch: dict | None) -> dict:
    """Shallow per-key merge; the kind's identity (`pinned`) fields stay pinned to base."""
    spec = _kind(kind)
    merged = {**(base or {}), **(patch or {})}
    for key in spec.pinned:
        if key in (base or {}):
            merged[key] = base[key]
    return merged


async def materialize_change(
    session: AsyncSession, *, tenant_id: uuid.UUID, device_id: uuid.UUID, created_by: uuid.UUID,
    template_id: uuid.UUID, kind: str, body: dict,
) -> ConfigChange:
    """Validate the effective body and materialize a draft config_change for the kind."""
    spec = _kind(kind)
    spec.validate(body or {})
    operation, target, payload = spec.to_change(body)
    change = await create_change(
        session, tenant_id=tenant_id, device_id=device_id, created_by=created_by,
        kind=spec.change_kind, operation=operation, target=target, payload=payload,
    )
    change.source_template_id = template_id
    await session.flush()
    return change
```
Keep `validate_alias_body`/`ALIAS_TYPES` defined ABOVE the `register_template_kind("firewall_alias", ...)` call. Remove the old `_VALIDATORS` and `_PINNED`. Update the module docstring (no longer "firewall_alias only").

- [ ] **Step 4: Run → PASS** (the new registry test + confirm no break: also run `tests/test_templates_service.py tests/test_profiles_service.py` — the alias materialize path must still pass through the registry).

- [ ] **Step 5: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/templates.py backend/tests/test_template_kind_registry.py
git commit -m "refactor(templates): kind-pluggable registry (TEMPLATE_KINDS) — firewall_alias unchanged

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Change-kind apply dispatch (`services/config_apply.py` + `config_push.apply_change`)

**Files:** Create `backend/app/services/config_apply.py`, `backend/tests/test_config_apply_dispatch.py`; Modify `backend/app/services/config_push.py`.

**Context:** `config_push.apply_change` (~line 123) hardcodes `res = await client.apply_alias(change.operation, change.payload, dry_run=not live)`. Replace with a dispatch by `change.kind` through a registry, so new config_change kinds (from M3b/c/d) register their connector applier without editing `apply_change`.

- [ ] **Step 1: Write `backend/tests/test_config_apply_dispatch.py`:**
```python
import pytest

from app.services import config_apply as ca


async def test_alias_applier_is_registered_and_dispatches():
    calls = {}

    class FakeClient:
        async def apply_alias(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "result": "ok"}

    res = await ca.apply_for_kind(FakeClient(), "alias", "set", {"name": "a"}, dry_run=True)
    assert calls["args"] == ("set", {"name": "a"}, True)
    assert res["result"] == "ok"


async def test_unknown_kind_raises():
    with pytest.raises(ca.UnknownChangeKindError):
        await ca.apply_for_kind(object(), "nope", "set", {}, dry_run=True)


async def test_register_a_custom_applier():
    async def _applier(client, operation, payload, *, dry_run):
        return {"applied": operation, "dry_run": dry_run}

    ca.register_change_applier("custom_demo", _applier)
    try:
        res = await ca.apply_for_kind(object(), "custom_demo", "set", {}, dry_run=False)
        assert res == {"applied": "set", "dry_run": False}
    finally:
        ca.CHANGE_APPLIERS.pop("custom_demo", None)
```

- [ ] **Step 2: Run → FAIL** (ModuleNotFoundError).

- [ ] **Step 3: Implement `backend/app/services/config_apply.py`:**
```python
"""Dispatch a config_change to the connector write for its kind.

Each config_change.kind registers an async applier `(client, operation, payload, *, dry_run) -> dict`.
M1's `alias` kind maps to the connector's `apply_alias`; new kinds (M3b+) register their own."""
from typing import Awaitable, Callable

Applier = Callable[..., Awaitable[dict]]


class UnknownChangeKindError(Exception):
    """No applier registered for a config_change kind."""


CHANGE_APPLIERS: dict[str, Applier] = {}


def register_change_applier(change_kind: str, applier: Applier) -> None:
    CHANGE_APPLIERS[change_kind] = applier


async def apply_for_kind(client, change_kind: str, operation: str, payload: dict, *, dry_run: bool) -> dict:
    applier = CHANGE_APPLIERS.get(change_kind)
    if applier is None:
        raise UnknownChangeKindError(f"no applier for config change kind: {change_kind}")
    return await applier(client, operation, payload, dry_run=dry_run)


# --- alias (M1): the verified firewall-alias write ---
async def _apply_alias(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_alias(operation, payload, dry_run=dry_run)


register_change_applier("alias", _apply_alias)
```

- [ ] **Step 4: Wire it into `config_push.apply_change`** — replace the hardcoded line:
```python
        res = await client.apply_alias(change.operation, change.payload, dry_run=not live)
```
with:
```python
        res = await apply_for_kind(client, change.kind, change.operation, change.payload, dry_run=not live)
```
and add the import at the top of `config_push.py`: `from app.services.config_apply import apply_for_kind`. (Watch for import cycles: `config_apply` imports nothing from `config_push`/`templates`, so this is safe.) The `except OpnsenseError` stays — but note an `UnknownChangeKindError` would NOT be caught by it; that's correct (an unregistered kind is a programming error, not a device failure — let it surface). Existing alias changes are unaffected (the registry returns `_apply_alias`).

- [ ] **Step 5: Run → PASS.** Then run the config-push apply tests to confirm no regression: `tests/test_config_push_apply.py tests/test_config_push_service.py tests/test_config_apply_dispatch.py`.

- [ ] **Step 6: Commit:**
```bash
cd /home/l0rdg3x/coding/OPNGMS
git add backend/app/services/config_apply.py backend/app/services/config_push.py backend/tests/test_config_apply_dispatch.py
git commit -m "refactor(config-push): kind-pluggable apply dispatch (CHANGE_APPLIERS) — alias unchanged

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] Full backend suite green: `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q` (all M1/M2 template/profile/config-push/alias tests still pass through the registries). `ruff check app/` clean.
- [ ] Final holistic review (focus: behavior-identical for firewall_alias/alias; the registries are the only structural change; no import cycle), then superpowers:finishing-a-development-branch → PR.

---

## Self-Review (author)

**Spec coverage:** the two hardcoded dispatch points (`materialize_change`'s `firewall_alias`→alias branch; `apply_change`'s `apply_alias` call) become registries with a `register_*` API and the `firewall_alias`/`alias` entry pre-seeded (Tasks 1-2). `effective_body`'s pinned fields become per-kind. Extensibility is proven by a throwaway-kind test in each task. No new tables/connector/frontend (M3b+ add the concrete kinds).

**Placeholder scan:** complete code in both tasks; the registry dataclass + functions are fully specified; the wiring change in `config_push` is a one-line swap + one import.

**Type consistency:** `materialize_change` keeps its signature (callers in `services/profiles.py` + `api/templates.py` unchanged); `effective_body`/`validate_body` keep their signatures; `apply_for_kind(client, change_kind, operation, payload, *, dry_run)` matches the `config_push` call site; `TemplateKind`/`register_template_kind`/`TEMPLATE_KINDS` and `CHANGE_APPLIERS`/`register_change_applier`/`apply_for_kind`/`UnknownChangeKindError` are the public surface M3b/c/d build on.
