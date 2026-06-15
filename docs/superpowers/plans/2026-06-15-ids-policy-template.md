# IDS Policy Template Kind — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a curated MSP template kind `ids_policy` (Suricata rule-action tuning policy): a new
`config_change.kind`, a connector `apply_ids_policy` (add/set/delete, upsert by description), and a
template-library form across all 12 locales.

**Architecture:** Same registry pattern as `suricata_ruleset`/`firewall_rule`/`monit_test` — a
`TemplateKind` + a `register_change_applier`, dispatched through the existing apply pipeline. The
connector resolves ruleset filenames → enabled file-uuids via the policy model's relation option map.

**Tech Stack:** Python 3.14 / FastAPI / pytest (fake-client unit tests); React 19 / TS / Mantine v9 /
Vitest. Spec: `docs/superpowers/specs/2026-06-15-ids-policy-template-design.md`.

---

## PR1 — Backend: `ids_policy` kind + connector

### Task 1: `ids_policy` template kind + applier registration

**Files:**
- Create: `backend/app/services/ids_policy_kind.py`
- Test: `backend/tests/test_ids_policy_kind.py`
- Modify: `backend/app/main.py` (import), `backend/app/worker.py` (import)

- [ ] **Step 1: Write the failing test** — `backend/tests/test_ids_policy_kind.py`

```python
import pytest

import app.services.ids_policy_kind  # noqa: F401  (registers on import)
from app.services import config_apply as ca
from app.services import templates as tpl

_GOOD = {
    "description": "Drop ET malware", "enabled": "1", "prio": "0",
    "action": ["alert", "drop"], "rulesets": ["abuse.ch.feodotracker.rules"],
    "content": {"severity": ["1", "2"]}, "new_action": "drop",
}


def test_ids_policy_kind_registered():
    spec = tpl.TEMPLATE_KINDS["ids_policy"]
    assert spec.change_kind == "ids_policy"
    op, target, payload = spec.to_change(_GOOD)
    assert op == "set" and target == "Drop ET malware" and payload["new_action"] == "drop"
    assert spec.pinned == ("description",)


def test_validate_accepts_good():
    tpl.validate_body("ids_policy", _GOOD)


def test_validate_accepts_minimal():
    tpl.validate_body("ids_policy", {"description": "p", "new_action": "alert"})


@pytest.mark.parametrize("patch", [
    {"description": ""},                 # identity required
    {"enabled": "yes"},                  # bad enabled
    {"prio": "high"},                    # non-int prio
    {"action": ["nope"]},                # bad action member
    {"action": "alert"},                 # action not a list
    {"new_action": "explode"},           # bad new_action
    {"rulesets": ["../etc/passwd"]},     # bad ruleset filename
    {"content": {"severity": "1"}},      # content value not a list
    {"content": [1, 2]},                 # content not a dict
])
def test_validate_rejects_bad(patch):
    with pytest.raises(tpl.InvalidTemplateError):
        tpl.validate_body("ids_policy", {**_GOOD, **patch})


async def test_applier_dispatches():
    calls = {}

    class FakeClient:
        async def apply_ids_policy(self, operation, payload, *, dry_run):
            calls["args"] = (operation, payload, dry_run)
            return {"dry_run": dry_run, "operation": "add"}

    await ca.apply_for_kind(FakeClient(), "ids_policy", "set", _GOOD, dry_run=True)
    assert calls["args"][0] == "set" and calls["args"][2] is True
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_ids_policy_kind.py -q`
Expected: FAIL (module `app.services.ids_policy_kind` does not exist).

- [ ] **Step 3: Implement `backend/app/services/ids_policy_kind.py`**

```python
"""Register the curated `ids_policy` template kind + its config-change applier.

A template body is a portable Suricata/IDS policy (rule-action tuning): identity = `description`;
the connector upserts by description. `rulesets` are referenced by filename and resolved to the
device's enabled ruleset-file uuids at apply time."""
import re

from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

_ACTIONS = {"disable", "alert", "drop"}                       # current-action match set
_NEW_ACTIONS = {"default", "alert", "drop", "disable"}
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")         # mirror the connector's charset guard
_INT_RE = re.compile(r"\A-?\d+\Z")


def _validate(body: dict) -> None:
    body = body or {}
    if not str(body.get("description", "")).strip():
        raise InvalidTemplateError("ids policy 'description' is required (it is the policy identity)")
    if str(body.get("enabled", "1")) not in ("0", "1"):
        raise InvalidTemplateError("ids policy 'enabled' must be '0' or '1'")
    if not _INT_RE.match(str(body.get("prio", "0"))):
        raise InvalidTemplateError("ids policy 'prio' must be an integer")
    actions = body.get("action", [])
    if not isinstance(actions, list) or any(a not in _ACTIONS for a in actions):
        raise InvalidTemplateError(f"ids policy 'action' must be a list of {sorted(_ACTIONS)}")
    if body.get("new_action", "alert") not in _NEW_ACTIONS:
        raise InvalidTemplateError(f"ids policy 'new_action' must be one of {sorted(_NEW_ACTIONS)}")
    rulesets = body.get("rulesets", [])
    if not isinstance(rulesets, list) or any(
        not isinstance(n, str) or not _RULESET_NAME_RE.match(n) for n in rulesets
    ):
        raise InvalidTemplateError("ids policy 'rulesets' must be a list of ruleset filenames")
    content = body.get("content", {})
    if not isinstance(content, dict) or any(
        not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(x, str) for x in v)
        for k, v in content.items()
    ):
        raise InvalidTemplateError("ids policy 'content' must be an object of metadata-key -> [values]")


register_template_kind("ids_policy", TemplateKind(
    validate=_validate,
    change_kind="ids_policy",
    to_change=lambda body: ("set", str(body.get("description", "")), body),
    pinned=("description",),
))


async def _apply_ids_policy(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_ids_policy(operation, payload, dry_run=dry_run)


register_change_applier("ids_policy", _apply_ids_policy)
```

- [ ] **Step 4: Wire startup imports**

In `backend/app/main.py`, next to the other `import app.services.*_kind` lines, add:
```python
import app.services.ids_policy_kind  # noqa: F401  — registers ids_policy kind at API-process startup
```
In `backend/app/worker.py`, next to the other `_kind` imports, add:
```python
import app.services.ids_policy_kind  # noqa: F401  — registers ids_policy kind at worker-process startup
```

- [ ] **Step 5: Run the test to confirm it passes**

Run: `cd backend && python -m pytest tests/test_ids_policy_kind.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/ids_policy_kind.py backend/tests/test_ids_policy_kind.py \
        backend/app/main.py backend/app/worker.py
git commit -m "feat(ids): add ids_policy curated template kind"
```

### Task 2: Connector `apply_ids_policy` (add/set/delete) + resolvers

**Files:**
- Modify: `backend/app/connectors/opnsense/client.py` (add `apply_ids_policy`, `_resolve_policy_uuid`,
  `_resolve_ruleset_file_uuids`, `_serialize_policy` near `apply_ids_rulesets`)
- Test: `backend/tests/test_client_ids_policy.py`

First READ `backend/app/connectors/opnsense/client.py` around `apply_ids_rulesets`
(lines ~369–396) and the existing resolvers (`_resolve_rule_uuid` ~301, `_resolve_monit_test_uuid`
~358) and `apply_grid_item` (~250, for the `_safe_uuid`/`_OPN_UUID_RE` patterns) to match style,
the `_post`/`_get` helpers, `RECONFIGURE_TIMEOUT`, `_safe_endpoint`, `_safe_uuid`, `_ruleset_name`,
`ApiError`.

- [ ] **Step 1: Write the failing test** — `backend/tests/test_client_ids_policy.py`

Build the test with a fake transport that records `_post`/`_get` calls. Model it on the existing
connector tests (find one with `grep -rl "OpnsenseClient(" backend/tests` and mirror its client
construction + monkeypatching of `_post`/`_get`). The test must assert:

```python
# pseudocode of the assertions — adapt to the repo's existing connector-test harness
_BODY = {"description": "Drop ET", "enabled": "1", "prio": "0",
         "action": ["alert", "drop"], "rulesets": ["et.rules"],
         "content": {"severity": ["1"]}, "new_action": "drop"}

async def test_dry_run_no_mutation(client):
    res = await client.apply_ids_policy("set", _BODY, dry_run=True)
    assert res["dry_run"] is True
    assert client.posted == []          # nothing was POSTed

async def test_set_adds_when_absent(client_absent):
    # searchPolicy -> {"rows": []}; getPolicy.rulesets maps et.rules -> "uuid-et"
    await client_absent.apply_ids_policy("set", _BODY, dry_run=False)
    paths = [p for p, _ in client_absent.posted]
    assert "ids/settings/addPolicy" in paths
    assert "ids/service/reconfigure" in paths
    policy = client_absent.posted_body("ids/settings/addPolicy")["policy"]
    assert policy["action"] == "alert,drop"          # comma-joined
    assert policy["rulesets"] == "uuid-et"           # filename resolved to uuid
    assert policy["new_action"] == "drop"

async def test_set_updates_when_present(client_present):
    # searchPolicy -> one row uuid "p1" matching description
    await client_present.apply_ids_policy("set", _BODY, dry_run=False)
    assert any(p == "ids/settings/setPolicy/p1" for p, _ in client_present.posted)

async def test_delete(client_present):
    await client_present.apply_ids_policy("delete", _BODY, dry_run=False)
    assert any(p == "ids/settings/delPolicy/p1" for p, _ in client_present.posted)

async def test_unknown_ruleset_raises(client_absent_no_ruleset):
    with pytest.raises(ApiError):
        await client_absent_no_ruleset.apply_ids_policy("set", _BODY, dry_run=False)

async def test_ambiguous_description_raises(client_two_matches):
    with pytest.raises(ApiError):
        await client_two_matches.apply_ids_policy("set", _BODY, dry_run=False)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && python -m pytest tests/test_client_ids_policy.py -q`
Expected: FAIL (`apply_ids_policy` not defined).

- [ ] **Step 3: Implement the connector methods** (place near `apply_ids_rulesets`)

```python
async def apply_ids_policy(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
    """Upsert an IDS policy by `description` (or delete it), then reload Suricata.

    Identity = description (1 match -> setPolicy; none -> addPolicy; many -> refuse). `rulesets`
    filenames are resolved to the device's ENABLED ruleset-file uuids; an absent/disabled ruleset
    raises ApiError (never a partial apply). dry_run performs NO mutation. RUNTIME VERIFICATION
    REQUIRED for the rulesets/content serialization (no policies/rules on the box to confirm against)."""
    description = str(payload.get("description", ""))
    if dry_run:
        return {"dry_run": True, "operation": operation, "description": description}
    if operation == "delete":
        uuid_ = await self._resolve_policy_uuid(description)
        if uuid_ is None:
            return {"dry_run": False, "operation": "delete", "result": "absent"}
        res = await self._post(f"ids/settings/delPolicy/{_safe_uuid(uuid_)}", {})
        await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": "delete", "result": res}
    policy = await self._serialize_policy(payload)
    uuid_ = await self._resolve_policy_uuid(description)
    if uuid_ is None:
        res = await self._post("ids/settings/addPolicy", {"policy": policy})
        op = "add"
    else:
        res = await self._post(f"ids/settings/setPolicy/{_safe_uuid(uuid_)}", {"policy": policy})
        op = "set"
    await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
    return {"dry_run": False, "operation": op, "result": res}

async def _resolve_policy_uuid(self, description: str) -> str | None:
    """Resolve an IDS policy by EXACT description. None if absent; ApiError if many (never mutate on doubt)."""
    if not description:
        raise ApiError(0, "ids policy description required (it is the policy identity)")
    data = await self._post(
        "ids/settings/searchPolicy", {"current": 1, "rowCount": 1000, "searchPhrase": description})
    matches = [r for r in data.get("rows", []) if r.get("description") == description]
    if len(matches) > 1:
        raise ApiError(0, f"ids policy '{description}' not uniquely resolvable ({len(matches)} matches)")
    return matches[0]["uuid"] if matches else None

async def _resolve_ruleset_file_uuids(self, filenames: list[str]) -> list[str]:
    """Map each ruleset FILENAME to its ENABLED file-uuid via the policy model's relation option map.

    GET ids/settings/getPolicy returns policy.rulesets as {file_uuid: {"value": filename, "selected": …}}
    for every enabled ruleset. A filename absent from that map is not enabled -> ApiError."""
    if not filenames:
        return []
    options = (await self._get("ids/settings/getPolicy")).get("policy", {}).get("rulesets", {})
    by_name: dict[str, str] = {}
    if isinstance(options, dict):
        for fuuid, meta in options.items():
            name = meta.get("value") if isinstance(meta, dict) else None
            if name:
                by_name[name] = fuuid
    out = []
    for name in filenames:
        self._ruleset_name(name)                       # charset guard
        uuid_ = by_name.get(name)
        if uuid_ is None:
            raise ApiError(0, f"ruleset '{name}' must be enabled before a policy can reference it")
        out.append(uuid_)
    return out

async def _serialize_policy(self, payload: dict) -> dict:
    """Build the OPNsense addPolicy/setPolicy body from a portable policy. Multi-fields are
    comma-joined; rulesets filenames are resolved to enabled file-uuids."""
    actions = payload.get("action", []) or []
    rulesets = await self._resolve_ruleset_file_uuids(payload.get("rulesets", []) or [])
    content = payload.get("content", {}) or {}
    return {
        "enabled": str(payload.get("enabled", "1")),
        "prio": str(payload.get("prio", "0")),
        "action": ",".join(actions),
        "rulesets": ",".join(rulesets),
        "content": _json.dumps(content) if content else "",
        "new_action": str(payload.get("new_action", "alert")),
        "description": str(payload.get("description", "")),
    }
```

Add `import json as _json` at the top of the module if no JSON import exists (else reuse the existing
one). Confirm `_safe_uuid` exists (used by `apply_grid_item`); if its signature differs, mirror the
exact call style already used there.

- [ ] **Step 4: Run the test to confirm it passes**

Run: `cd backend && python -m pytest tests/test_client_ids_policy.py -q`
Expected: PASS.

- [ ] **Step 5: Lint**

Run: `cd backend && ruff check app/connectors/opnsense/client.py app/services/ids_policy_kind.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/app/connectors/opnsense/client.py backend/tests/test_client_ids_policy.py
git commit -m "feat(ids): connector apply_ids_policy (upsert by description + delete)"
```

### Task 3: PR1 full backend suite + open PR

- [ ] **Step 1: Run the full backend suite** (needs the test DB env from AGENTS.md)

Run: `cd backend && python -m pytest -q`
Expected: all pass (the two new test files included). If the suite is slow, at minimum run
`pytest tests/test_ids_policy_kind.py tests/test_client_ids_policy.py -q` plus a smoke of the
template/apply tests touched.

- [ ] **Step 2: Lint the backend**

Run: `cd backend && ruff check app/`
Expected: clean.

- [ ] **Step 3: Push + open PR** (main is protected — PR only)

PR title: `feat(ids): ids_policy curated template kind + connector`
Body: summary (new kind + connector add/set/delete, upsert by description, runtime-verify caveat for
rulesets/content serialization), test plan (the two new test files), and a note that the live apply
path needs operator verification on the real box before `LIVE_PUSH_ENABLED`.

---

## PR2 — Frontend: `IdsPolicyForm` + modal wiring + i18n

> Branch off `main` AFTER PR1 merges. READ `frontend/src/templates/TemplateFormModal.tsx`,
> `MonitTestForm.tsx`, `IdsRulesetForm.tsx`, `settingHooks.ts` (for `useIdsRulesets`,
> `useTenantDevices`), and `frontend/src/i18n/en.ts` (the `templates` block) first.

### Task 4: `IdsPolicyForm.tsx`

**Files:**
- Create: `frontend/src/templates/IdsPolicyForm.tsx`
- Test: `frontend/src/templates/__tests__/idsPolicyForm.test.tsx`

- [ ] **Step 1: Write the failing test** — render `IdsPolicyForm` with a stub `value`/`onChange`,
  assert the description input, the action MultiSelect, the new_action Select, and the rulesets loader
  render (by `data-testid`), and that editing the description calls `onChange` with the new body.
  Mirror `__tests__/templateFormModal.test.tsx` for the render harness (MantineProvider + i18n).

- [ ] **Step 2: Run it to confirm it fails** — `cd frontend && npx vitest run src/templates/__tests__/idsPolicyForm.test.tsx`

- [ ] **Step 3: Implement `IdsPolicyForm.tsx`** — props `{ value: PolicyBody; onChange }` where
  `PolicyBody = { description: string; enabled: string; prio: string; action: string[]; rulesets:
  string[]; content: Record<string, string[]>; new_action: string }`. Fields:
  - `description` TextInput (required), `data-testid="idspolicy-description"`.
  - `enabled` Checkbox, `data-testid="idspolicy-enabled"`.
  - `prio` NumberInput, `data-testid="idspolicy-prio"`.
  - `action` MultiSelect over `["disable","alert","drop"]`, `data-testid="idspolicy-action"`.
  - `new_action` Select over `["default","alert","drop","disable"]`, `data-testid="idspolicy-newaction"`.
  - `rulesets` — reference-device Select + load button + MultiSelect, reusing `useTenantDevices` +
    `useIdsRulesets` exactly like `IdsRulesetForm` (show only `enabled` rows: filter
    `rows.filter(r => r.enabled === "1")`), `data-testid="idspolicy-rulesets"`.
  - `content` — a minimal "advanced" key→values editor (add-row of `{ key, comma-separated values }`),
    default empty; `data-testid="idspolicy-content"`. Keep it simple (most policies need none).
  - A dimmed note `t.templates.idsPolicy.note` (runtime-verify hint).
  All user-facing strings come from `t.templates.idsPolicy.*` (no hardcoded English).

- [ ] **Step 4: Run the test to confirm it passes** — `npx vitest run src/templates/__tests__/idsPolicyForm.test.tsx`

- [ ] **Step 5: Commit** — `git commit -m "feat(ids): IdsPolicyForm template form"`

### Task 5: Wire into `TemplateFormModal` + i18n (all 12 locales)

**Files:**
- Modify: `frontend/src/templates/TemplateFormModal.tsx`
- Modify: `frontend/src/i18n/en.ts` (+ `it es fr de pt nl ru ar zh zhTW ja`)

- [ ] **Step 1: Add the English keys** to `frontend/src/i18n/en.ts` under `templates`:
  `kindIdsPolicy` (the Select option label) and an `idsPolicy` block:
  `{ description, enabled, prio, action, newAction, rulesets, referenceDevice, load, loadHint,
     noDevice, noRulesets, loadFailed, content, contentKey, contentValues, addContent, note }`.

- [ ] **Step 2: Mirror the keys into all 12 locale dictionaries** (translated). Key parity is
  compiler-enforced by `tsc -b`, so every locale must have the identical key set. Dispatch one
  translation subagent per locale (or batch) to translate the `idsPolicy` block + `kindIdsPolicy`.

- [ ] **Step 3: Wire `TemplateFormModal.tsx`:**
  - import `IdsPolicyForm`;
  - `type PolicyBody = {…}; const EMPTY_POLICY: PolicyBody = { description:"", enabled:"1", prio:"0",
    action:[], rulesets:[], content:{}, new_action:"alert" };`
  - `const [policyBody, setPolicyBody] = useState<PolicyBody>(EMPTY_POLICY);`
  - in the `opened` `useEffect`, init `policyBody` from `editing?.kind === "ids_policy"` (else
    `EMPTY_POLICY`), mirroring the other `*Body` inits;
  - add the `else if (kind === "ids_policy")` submit branch: validate `description` non-empty (notify
    `t.templates.idsPolicy.descriptionRequired` or reuse a generic) and create/update with
    `{ kind: "ids_policy", name: v.name, description: v.description, body: policyBody }`;
  - add the `<Select>` option `{ value: "ids_policy", label: t.templates.kindIdsPolicy }`;
  - add the render dispatch `kind === "ids_policy" ? <IdsPolicyForm value={policyBody} onChange={setPolicyBody} /> : …`.

- [ ] **Step 4: Build (the gate) + tests + lint**

Run: `cd frontend && npm run build && npm test && npm run lint`
Expected: all green (build = `tsc -b && vite build`; key parity enforced).

- [ ] **Step 5: Commit + open PR**

```bash
git commit -m "feat(ids): wire ids_policy into the template form + i18n (12 locales)"
```
PR title: `feat(ids): ids_policy template form + i18n`. Body: summary + test plan (`npm run build`).

---

## Self-review notes (already applied)

- **Spec coverage:** Task 1 = kind+validation+applier; Task 2 = connector add/set/delete + resolvers;
  Task 4–5 = frontend form + wiring + i18n. The `delete` op (spec decision #4) is in Task 2 even though
  the kind only emits `set` — it makes the future Revert inverse builder trivial.
- **Runtime-verify hotspots** (rulesets/content `addPolicy` serialization) are unit-tested against the
  assumed shape and flagged in both PRs for the operator's live box verification.
- **No migration** — `ids_policy` reuses `templates` + `config_change`.
