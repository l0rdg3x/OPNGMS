# Revert — Inverse Builders for the Remaining Kinds — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement
> this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add inverse builders so the operator **Revert** works for `firewall_rule`, `monit_test`,
`ids_policy`, and `catalog_setting` — completing the deferred apply-pipeline-reliability follow-up.

**Architecture:** Each builder is a pure `(change, snapshot_xml) -> (operation, target, payload)`
registered in `INVERSE_BUILDERS` (`app/services/config_revert.py`). Identity present in the pre-apply
snapshot → `set`-restore the full prior record; absent → `delete`. `firewall_rule`/`monit_test` need a
new connector `delete` branch; `ids_policy` delete already exists; `catalog_setting` reuses grid `del`.
No API/frontend change (`revertible` auto-computes via `has_inverse`).

**Tech Stack:** Python 3.14 / pytest (pure builder tests + fake-transport connector tests). Spec:
`docs/superpowers/specs/2026-06-15-revert-inverse-builders-design.md`.

**Execution note:** 3 PRs — **PR1** = `firewall_rule` + `monit_test` (twins: shared helper + both builders
+ both connector deletes); **PR2** = `ids_policy`; **PR3** = `catalog_setting`.

---

## PR1 — `firewall_rule` + `monit_test` inverse builders + connector deletes

> READ first: `app/services/config_revert.py` (the registry, `alias_from_config_xml`,
> `setting_from_config_xml`, `_invert_alias`, `revert_change`), `app/services/firewall_rule_kind.py`,
> `app/services/monit_kind.py`, and in `app/connectors/opnsense/client.py` the methods
> `apply_firewall_rule` (~281), `_resolve_rule_uuid` (~301), `apply_monit_test` (~317),
> `_resolve_monit_test_uuid` (~358), plus the v0.13.0 uuid-guard pattern (`_OPN_UUID_RE` + `ApiError`).

### Task 1: `record_from_config_xml` helper + firewall_rule/monit_test builders

**Files:**
- Modify: `backend/app/services/config_revert.py`
- Test: `backend/tests/test_config_revert_firewall_monit.py`

- [ ] **Step 1: Write the failing test** — `backend/tests/test_config_revert_firewall_monit.py`

Build a minimal `config.xml` fixture string containing one MVC filter rule and one monit test, e.g.:
```python
import uuid as _uuid
import pytest
from app.models.config_change import ConfigChange
from app.services.config_revert import build_inverse, has_inverse, NoInverseError

_XML = """<opnsense>
  <OPNsense>
    <Firewall><Filter><rules>
      <rule uuid="r1"><description>tpl-rule</description><interface>lan</interface>
        <action>pass</action><direction>in</direction><ipprotocol>inet</ipprotocol>
        <source_net>any</source_net><destination_net>any</destination_net></rule>
    </rules></Filter></Firewall>
    <monit><test uuid="t1"><name>tpl-test</name><type>SystemResource</type>
      <condition>cpu usage is greater than 90%</condition><action>alert</action><path></path></test></monit>
  </OPNsense>
</opnsense>"""

def _change(kind, target, payload, op="set", status="applied"):
    c = ConfigChange()
    c.id = _uuid.uuid4(); c.kind = kind; c.target = target; c.payload = payload
    c.operation = op; c.status = status
    return c

def test_has_inverse_for_new_kinds():
    assert has_inverse("firewall_rule") and has_inverse("monit_test")

def test_firewall_rule_set_restore():
    ch = _change("firewall_rule", "tpl-rule", {"description": "tpl-rule", "interface": "lan"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "set" and target == "tpl-rule"
    assert payload["action"] == "pass" and payload["interface"] == "lan"

def test_firewall_rule_created_is_deleted():
    ch = _change("firewall_rule", "ghost", {"description": "ghost", "interface": "lan"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "delete" and payload == {"description": "ghost", "interface": "lan"}

def test_monit_test_set_restore():
    ch = _change("monit_test", "tpl-test", {"name": "tpl-test"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "set" and payload["type"] == "SystemResource" and payload["condition"]

def test_monit_test_created_is_deleted():
    ch = _change("monit_test", "ghost", {"name": "ghost"})
    op, target, payload = build_inverse(ch, _XML)
    assert op == "delete" and payload == {"name": "ghost"}

@pytest.mark.parametrize("kind,target,payload", [
    ("firewall_rule", "tpl-rule", {"description": "tpl-rule", "interface": "lan"}),
    ("monit_test", "tpl-test", {"name": "tpl-test"}),
])
def test_no_snapshot_raises(kind, target, payload):
    with pytest.raises(NoInverseError):
        build_inverse(_change(kind, target, payload), None)
```

- [ ] **Step 2: Run it — confirm it fails** (`firewall_rule`/`monit_test` not in `INVERSE_BUILDERS`).

Run: `cd backend && . .venv/bin/activate && python -m pytest tests/test_config_revert_firewall_monit.py -q`

- [ ] **Step 3: Implement in `app/services/config_revert.py`**

```python
def record_from_config_xml(xml: str, path: str, match: dict) -> dict | None:
    """Find the element under `path` whose child tags equal every (tag, value) in `match`;
    return its children as a flat {tag: text} dict, or None. (Generalizes alias_from_config_xml.)"""
    root = DET.fromstring(xml)
    for el in root.iterfind(f".//{path}"):
        if all((el.findtext(tag) or "") == val for tag, val in match.items()):
            return {child.tag: (child.text or "") for child in el}
    return None


_FW_RULE_PATH = "OPNsense/Firewall/Filter/rules/rule"


def _invert_firewall_rule(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    payload = change.payload or {}
    description = change.target or payload.get("description", "")
    interface = str(payload.get("interface", ""))
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the firewall rule from")
    prior = record_from_config_xml(snapshot_xml, _FW_RULE_PATH,
                                   {"description": description, "interface": interface})
    if prior is None:
        return "delete", description, {"description": description, "interface": interface}
    return "set", description, prior


register_inverse_builder("firewall_rule", _invert_firewall_rule)


_MONIT_TEST_PATH = "OPNsense/monit/test"


def _invert_monit_test(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    name = change.target or (change.payload or {}).get("name", "")
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the monit test from")
    prior = record_from_config_xml(snapshot_xml, _MONIT_TEST_PATH, {"name": name})
    if prior is None:
        return "delete", name, {"name": name}
    return "set", name, prior


register_inverse_builder("monit_test", _invert_monit_test)
```
(`DET` = `defusedxml.ElementTree`, already imported in the module. Note: `iterfind(".//OPNsense/Firewall/...")`
works because ElementTree path is relative to root; if the snapshot root is `<opnsense>`, use the path as
written. Verify the match against the test fixture and adjust the `.//` prefix if needed.)

- [ ] **Step 4: Run — confirm pass.** Same command as Step 2.

- [ ] **Step 5: Commit** — `git commit -m "feat(revert): inverse builders for firewall_rule + monit_test"`

### Task 2: Connector `delete` branches for firewall_rule + monit_test

**Files:**
- Modify: `backend/app/connectors/opnsense/client.py`
- Test: `backend/tests/test_client_rule_monit_delete.py`

- [ ] **Step 1: Write the failing test** (respx fake transport, mirroring `test_client_ids_policy.py`):
  `apply_firewall_rule("delete", {"description":"r","interface":"lan"}, dry_run=False)` resolves the uuid
  via `searchRule` (return one matching row) then POSTs `firewall/filter/delRule/{uuid}` + `firewall/filter/apply`;
  dry-run mutates nothing; absent rule → clean no-op. Same shape for `apply_monit_test("delete", {"name":"t"})`
  → `monit/settings/delTest/{uuid}` + `monit/service/reconfigure`.

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Implement the delete branches.**

In `apply_firewall_rule`, before the upsert logic, handle delete:
```python
if operation == "delete":
    if dry_run:
        return {"dry_run": True, "operation": "delete", "description": description}
    uuid_ = await self._resolve_rule_uuid(description, interface)
    if uuid_ is None:
        return {"dry_run": False, "operation": "delete", "result": "absent"}
    res = await self._post(f"firewall/filter/delRule/{uuid_}", {})   # uuid is box-sourced from searchRule
    await self._post("firewall/filter/apply", {}, timeout=RECONFIGURE_TIMEOUT)
    return {"dry_run": False, "operation": "delete", "result": res}
```
(`_resolve_rule_uuid` already validates/searches by (description, interface); it returns the box uuid —
embed it directly as `apply_firewall_rule`'s existing setRule path does. Keep dry-run before any mutation.)

In `apply_monit_test`, analogously (note its `attach_to_system` pop happens for upserts — delete should
short-circuit before that):
```python
if operation == "delete":
    name = str(payload.get("name", ""))
    if dry_run:
        return {"dry_run": True, "operation": "delete", "name": name}
    uuid_ = await self._resolve_monit_test_uuid(name)
    if uuid_ is None:
        return {"dry_run": False, "operation": "delete", "result": "absent"}
    res = await self._post(f"monit/settings/delTest/{uuid_}", {})
    await self._post("monit/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
    return {"dry_run": False, "operation": "delete", "result": res}
```
Place the monit delete branch at the very top of the method (before `payload = dict(payload)` / the
`attach_to_system` pop), reading `name` from the raw payload. Document the "does not detach from a
service" limitation in the docstring.

- [ ] **Step 4: Run — confirm pass.**

- [ ] **Step 5: Lint** — `ruff check app/connectors/opnsense/client.py app/services/config_revert.py`

- [ ] **Step 6: Commit** — `git commit -m "feat(revert): connector delRule/delTest for the revert path"`

### Task 3: PR1 suite + open PR
- [ ] Run `python -m pytest tests/test_config_revert*.py tests/test_client_rule_monit_delete.py tests/test_config_revert.py -q` (+ a smoke of the existing revert/alias tests) and `ruff check app/`. Push + open PR `feat(revert): firewall_rule + monit_test revert`. Note `delRule`/`delTest` need operator live-verification.

---

## PR2 — `ids_policy` inverse builder

> READ: `app/services/ids_policy_kind.py` (the body shape) and the v0.13.0 `apply_ids_policy` (it already
> has `delete`). The policy stores rulesets as file-uuids; map them back to filenames from
> `OPNsense/IDS/files/file` in the SAME snapshot.

### Task 4: `_invert_ids_policy`

**Files:**
- Modify: `backend/app/services/config_revert.py`
- Test: `backend/tests/test_config_revert_ids_policy.py`

- [ ] **Step 1: Failing test** — fixture `config.xml` with `OPNsense/IDS/files/file uuid="fA"` →
  `<filename>et.rules</filename>` and an `OPNsense/IDS/policies/policy` with `<description>p1</description>`,
  `<action>alert,drop</action>`, `<rulesets>fA</rulesets>`, `<content>{"severity":["1"]}</content>`,
  `<new_action>drop</new_action>`, `<enabled>1</enabled>`, `<prio>0</prio>`. Assert: set-restore yields
  `("set", "p1", body)` with `body["action"] == ["alert","drop"]`, `body["rulesets"] == ["et.rules"]`
  (uuid→filename), `body["content"] == {"severity":["1"]}`; a created policy (absent) → `("delete", "p1",
  {"description":"p1"})`; no snapshot → `NoInverseError`.

- [ ] **Step 2: Run — confirm fail.**

- [ ] **Step 3: Implement `_invert_ids_policy`** in `config_revert.py`:
```python
import json

_IDS_POLICY_PATH = "OPNsense/IDS/policies/policy"
_IDS_FILES_PATH = "OPNsense/IDS/files/file"


def _ids_files_map(xml: str) -> dict:
    """Build {file_uuid: filename} from the IDS files table in the snapshot."""
    root = DET.fromstring(xml)
    out = {}
    for f in root.iterfind(f".//{_IDS_FILES_PATH}"):
        u = f.get("uuid"); name = f.findtext("filename")
        if u and name:
            out[u] = name
    return out


def _invert_ids_policy(change: ConfigChange, snapshot_xml: str | None) -> tuple[str, str, dict]:
    description = change.target or (change.payload or {}).get("description", "")
    if not snapshot_xml:
        raise NoInverseError("no pre-apply snapshot to reconstruct the ids policy from")
    prior = record_from_config_xml(snapshot_xml, _IDS_POLICY_PATH, {"description": description})
    if prior is None:
        return "delete", description, {"description": description}
    files = _ids_files_map(snapshot_xml)
    body = {
        "description": description,
        "enabled": prior.get("enabled", "1"),
        "prio": prior.get("prio", "0"),
        "new_action": prior.get("new_action", "alert"),
        "action": [a for a in (prior.get("action", "") or "").split(",") if a],
        "rulesets": [files[u] for u in (prior.get("rulesets", "") or "").split(",") if u in files],
        "content": json.loads(prior["content"]) if prior.get("content") else {},
    }
    return "set", description, body


register_inverse_builder("ids_policy", _invert_ids_policy)
```
(Reuse the module-level `import gzip`/existing imports; add `import json` if not present.)

- [ ] **Step 4: Run — confirm pass.** **Step 5: Commit** — `feat(revert): inverse builder for ids_policy`.
- [ ] **Step 6:** suite + ruff + push + open PR `feat(revert): ids_policy revert`.

---

## PR3 — `catalog_setting` inverse builder

> READ: `app/services/catalog_kind.py` (the payload shape: scalars + grids + paths), `app/api/catalog.py`
> `_build_payload` (~line 75), `app/services/config_revert.py setting_from_config_xml`, and how
> `change.result` stores the grid op results (`config_push.apply_change`: `change.result = res` where res
> is the applier return → `result["grids"][i]["result"]["uuid"]` for an added row).

### Task 5: add `xml_path` to the catalog payload
- [ ] In `app/api/catalog.py _build_payload`, include `"xml_path": model.get("xml_path", "")` in the
  returned dict. Update/extend the catalog payload test to assert it. Commit
  `feat(catalog): carry xml_path in the change payload (for revert)`.

### Task 6: `_invert_catalog_setting`
**Files:** Modify `backend/app/services/config_revert.py`; Test `backend/tests/test_config_revert_catalog.py`.
- [ ] **Step 1: Failing test** — a `catalog_setting` change with `payload={model_id, set_path,
  reconfigure_path, model_root, xml_path:"OPNsense/Unbound", scalars:{"general.x":"new"},
  grids:[{op:"add",endpoints,row:"host",uuid:None,item:{...}}, {op:"set",endpoints,row:"host",uuid:"u1",
  item:{...}}, {op:"del",endpoints,row:"host",uuid:"u2"}]}` and a matching `change.result` (the add op's
  result carries `uuid:"uNEW"`), plus a snapshot with prior scalar value and the `u1`/`u2` rows. Assert the
  inverse is `("set", model_id, payload)` where: `scalars` restore the prior value; the inverted grids are
  add→`del uNEW`, set→`set u1` prior fields, del→`add` u2's prior fields; reversed order; only ops with a
  live result are inverted.
- [ ] **Step 2-4:** implement `_invert_catalog_setting` (scalars via `setting_from_config_xml(snapshot,
  xml_path, scalars.keys())`; grid inversion finding rows by uuid in the snapshot subtree under `xml_path`;
  `add→del` using `change.result["grids"][i]["result"]["uuid"]`); register it; run; **no snapshot** →
  `NoInverseError` when scalars or del/set grids are present. Commit `feat(revert): inverse builder for
  catalog_setting`.
- [ ] **Step 5:** full revert/catalog suite + ruff + push + open PR `feat(revert): catalog_setting revert`.

---

## Self-review notes
- All four builders are pure + snapshot-only (PR2's ids_policy uuid→filename map is read from the same
  snapshot). No API/frontend change. `ids_rulesets` stays out (button disabled).
- `delRule`/`delTest` are new connector writes → flagged for operator live-verification in the PR1 body.
- `record_from_config_xml` (PR1) is reused by ids_policy (PR2) — PR2 depends on PR1 being merged.
