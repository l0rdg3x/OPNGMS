# IDS Policy Template Kind — Design Spec

**Date:** 2026-06-15
**Status:** Approved (design); writing implementation plan next.
**Covers:** A new curated MSP template kind `ids_policy` that lets an operator define a Suricata/IDS
**policy** (rule-action tuning) once in the template library and apply it to many devices — the same
"library + per-tenant override + typed apply" model as `suricata_ruleset`, `firewall_rule`, `monit_test`.

## Goal

OPNGMS can already enable IDS **ruleset files** (`suricata_ruleset` → `ids_rulesets`). It cannot express
an IDS **policy**: a rule-tuning entry that says "for rules matching {rulesets, current-action, metadata
filters}, change the action to {alert|drop|disable} with {priority}". Policies are the standard OPNsense
mechanism for turning a noisy ruleset into a useful one (e.g. "drop everything in the ET-malware category,
alert-only on info-severity"). This milestone adds `ids_policy` as a first-class curated template kind.

## Verified facts (real box 192.168.1.82, read-only)

- An IDS policy is the `OPNsense/IDS/policies/policy` ArrayField grid. Fields (from `GET ids/settings/getPolicy`):
  `enabled` ("1"), `prio` ("0"), `action` (multi OptionField over `{disable, alert, drop}` — the *current*
  action of rules this policy matches), `rulesets` (multi ModelRelation to **enabled** ruleset files),
  `content` (multi PolicyContentField — rule-metadata filters), `new_action` (single OptionField over
  `{default, alert, drop, disable}`, default `alert`), `description`.
- `ids/settings/searchPolicy` (read) works and returns `{"rows":[],...}` (no policies on the box).
- Ruleset files are identified by **filename** in `listRulesets` (no uuid there). In config a *touched*
  ruleset becomes `OPNsense/IDS/files/file/<file uuid=…>` with `filename` + `enabled`. The policy
  `rulesets` ModelRelation references those **file uuids**, and the model filters the relation to
  `enabled=/1/` — i.e. **a policy can only reference rulesets that are already enabled** on the device.
- `listRuleMetadata` exists (the `content` option source) but is empty until rules are downloaded — the
  available `content` values are device/state-dependent.

**Implication (portability):** the portable, fully-verifiable core is `description, enabled, prio,
action[], new_action`. `rulesets[]` is portable **by filename** but must be resolved to the device's
file-uuid at apply time (and the ruleset must be enabled first). `content[]` is portable as raw
metadata key→values. The exact `addPolicy` serialization of `rulesets`/`content` carries a **RUNTIME
VERIFICATION REQUIRED** caveat (no policy/rules on the box to confirm against now) — verified live by the
operator before flipping `LIVE_PUSH_ENABLED`, exactly as every prior curated kind was verified.

## Locked decisions (from brainstorming)

1. **New curated template kind `ids_policy`** → new `config_change.kind = "ids_policy"`. Same registry
   pattern as the other curated kinds (`register_template_kind` + `register_change_applier`).
2. **Scope = the complete policy** (user choice): `description, enabled, prio, action[], rulesets[],
   content[], new_action`.
3. **Identity = `description`.** `to_change = ("set", description, body)`; `pinned = ("description",)`.
   The connector upserts by exact description (1 match → setPolicy; 0 → addPolicy; >1 → refuse).
4. **The connector supports add/set/delete from the start** (even though the kind only emits `set`), so
   the upcoming Revert milestone's `ids_policy` inverse builder (grid-clean) works with no extra connector
   change. `delete` = `delPolicy/{uuid}`.
5. **`rulesets[]` is stored as filenames** and resolved to the device's enabled file-uuids at apply; a
   referenced ruleset that is absent/not-enabled → a clear `ApiError` (never a silent partial apply).
6. **Ships dry-run-safe** behind `LIVE_PUSH_ENABLED`. Unit-tested against a fake client; the live
   `rulesets`/`content` serialization is the operator's runtime-verify step (documented caveat).
7. **`ids_rulesets` revert and IDS policy *revert*** are out of scope here — they belong to the paused
   Revert milestone ([[revert-inverse-builders-design]]), which will add an `ids_policy` inverse builder.

## Architecture

```
 Template library ──"ids_policy"──▶ TemplateKind(validate, change_kind="ids_policy",
                                                  to_change=("set", description, body), pinned=("description",))
        │  apply to device
        ▼
 materialize_change → config_change(kind="ids_policy", operation="set", target=description, payload=body)
        │  apply pipeline (preview → lock → snapshot → apply_for_kind)
        ▼
 _apply_ids_policy(client, "set", body, dry_run) ─▶ client.apply_ids_policy("set", body, dry_run)
        ▼
 OPNsense:  searchPolicy(description) → addPolicy | setPolicy/{uuid}   (delete → delPolicy/{uuid})
            rulesets: filename → enabled file-uuid  ·  ids/service/reconfigure
```

## Component 1 — Template kind (`app/services/ids_policy_kind.py`)

Mirrors `app/services/monit_kind.py` / `ids_kind.py`.

```python
_ACTIONS = {"disable", "alert", "drop"}          # current-action match set
_NEW_ACTIONS = {"default", "alert", "drop", "disable"}
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")   # mirror the connector's charset guard

def _validate(body: dict) -> None:
    body = body or {}
    if not str(body.get("description", "")).strip():
        raise InvalidTemplateError("ids policy 'description' is required (it is the policy identity)")
    en = str(body.get("enabled", "1"))
    if en not in ("0", "1"):
        raise InvalidTemplateError("ids policy 'enabled' must be '0' or '1'")
    prio = str(body.get("prio", "0"))
    if not re.fullmatch(r"-?\d+", prio):
        raise InvalidTemplateError("ids policy 'prio' must be an integer")
    actions = body.get("action", [])
    if not isinstance(actions, list) or any(a not in _ACTIONS for a in actions):
        raise InvalidTemplateError(f"ids policy 'action' must be a list ⊆ {sorted(_ACTIONS)}")
    if body.get("new_action", "alert") not in _NEW_ACTIONS:
        raise InvalidTemplateError(f"ids policy 'new_action' must be one of {sorted(_NEW_ACTIONS)}")
    rs = body.get("rulesets", [])
    if not isinstance(rs, list) or any(not isinstance(n, str) or not _RULESET_NAME_RE.match(n) for n in rs):
        raise InvalidTemplateError("ids policy 'rulesets' must be a list of ruleset filenames")
    content = body.get("content", {})
    if not isinstance(content, dict) or any(
        not isinstance(k, str) or not isinstance(v, list) or any(not isinstance(x, str) for x in v)
        for k, v in content.items()
    ):
        raise InvalidTemplateError("ids policy 'content' must be an object of metadata-key → [values]")

register_template_kind("ids_policy", TemplateKind(
    validate=_validate,
    change_kind="ids_policy",
    to_change=lambda body: ("set", str(body.get("description", "")), body),
    pinned=("description",),
))

async def _apply_ids_policy(client, operation, payload, *, dry_run):
    return await client.apply_ids_policy(operation, payload, dry_run=dry_run)

register_change_applier("ids_policy", _apply_ids_policy)
```

Registered at startup by importing the module in **`app/main.py`** and **`app/worker.py`** (next to the
other `_kind` imports).

## Component 2 — Connector (`app/connectors/opnsense/client.py`)

```python
async def apply_ids_policy(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
    """Upsert an IDS policy by `description` (or delete it), then reload Suricata.

    Identity = description (1 match -> setPolicy; none -> addPolicy; many -> refuse). `rulesets`
    filenames are resolved to the device's ENABLED ruleset-file uuids (absent/disabled -> ApiError).
    dry_run performs NO mutation. RUNTIME VERIFICATION REQUIRED for the rulesets/content serialization."""
    description = str(payload.get("description", ""))
    if dry_run:
        return {"dry_run": True, "operation": operation, "description": description}
    if operation == "delete":
        uuid_ = await self._resolve_policy_uuid(description)
        if uuid_ is None:
            return {"dry_run": False, "operation": "delete", "result": "absent"}
        res = await self._post(f"ids/settings/delPolicy/{_safe_uuid(uuid_)}", {})
    else:
        policy = await self._serialize_policy(payload)          # builds the {policy: {...}} body
        uuid_ = await self._resolve_policy_uuid(description)
        if uuid_ is None:
            res = await self._post("ids/settings/addPolicy", {"policy": policy}); op = "add"
        else:
            res = await self._post(f"ids/settings/setPolicy/{_safe_uuid(uuid_)}", {"policy": policy}); op = "set"
    await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
    return {"dry_run": False, "operation": op if operation != "delete" else "delete", "result": res}
```

Helpers:
- `_resolve_policy_uuid(description)` — `POST ids/settings/searchPolicy {current:1, rowCount:1000,
  searchPhrase: description}`, filter to exact `description`; `None` if absent; `ApiError` if >1
  (mirror `_resolve_rule_uuid`/`_resolve_monit_test_uuid`).
- `_resolve_ruleset_file_uuids(filenames)` — resolve each ruleset **filename → enabled file-uuid**
  using the policy model's own relation option map: `GET ids/settings/getPolicy` returns
  `policy.rulesets` as `{file_uuid: {"value": <filename>, "selected": …}}` for every **enabled**
  ruleset (the model filters the relation to `enabled=/1/`). Build `{filename: uuid}` from that map and
  look up each requested filename. A filename not in the map → not enabled → `ApiError("ruleset
  '<name>' must be enabled before a policy can reference it")`. API-only (no config.xml access). The
  *exact populated shape* is the runtime-verify hotspot (the box currently has 0 enabled rulesets, so
  `policy.rulesets` reads as `[]`); the option-map shape above is the standard OPNsense form-field
  encoding (same shape as `action`/`new_action` already observed on this box).
- `_serialize_policy(payload)` — builds the POST dict: scalars `enabled, prio, new_action, description`
  passed through; `action` → `",".join(payload["action"])`; `rulesets` → `",".join(resolved_uuids)`;
  `content` → the PolicyContentField shape (RUNTIME VERIFY; v1 assumes a JSON-encoded `{key:[values]}`).
- charset-validate every embedded uuid (`_safe_uuid`) and ruleset filename (`_ruleset_name`) before URL use.

**Outbound safety:** all calls go through the existing SSRF-guarded `_post`/`_get`. No new outbound path.

## Component 3 — Frontend (`frontend/src/templates/`)

- New **`IdsPolicyForm.tsx`** (value/onChange of the policy body), mirroring `MonitTestForm`/`IdsRulesetForm`:
  - `description` (TextInput, required — the identity).
  - `enabled` (Checkbox).
  - `prio` (NumberInput).
  - `action` (MultiSelect over `disable/alert/drop`).
  - `new_action` (Select over `default/alert/drop/disable`).
  - `rulesets` (MultiSelect; load enabled rulesets from a chosen reference device via the existing
    `useIdsRulesets` hook, like `IdsRulesetForm` — show only `enabled` rows).
  - `content` (v1: an "advanced" key→values editor; a simple add-row UI of `{ key, comma-values }`,
    default empty — most policies need none).
- Wire into **`TemplateFormModal.tsx`**: add `policyBody` state + `EMPTY_POLICY`, the `opened` init
  branch, the `ids_policy` submit branch (create/update with `kind: "ids_policy"`, `body: policyBody`),
  the `<Select>` option `{ value: "ids_policy", label: t.templates.kindIdsPolicy }`, and the render
  dispatch `kind === "ids_policy" ? <IdsPolicyForm .../> : …`.
- **i18n:** add `templates.kindIdsPolicy` + a `templates.idsPolicy.*` block (labels, hints, the
  reference-device loader strings, the runtime-verify note) to `en.ts` **and mirror into all 12 locales**
  (`it es fr de pt nl ru ar zh zhTW ja`) — key parity is compiler-enforced (`tsc -b`).

## Data model

No schema change. `ids_policy` reuses the existing `templates` table (kind + JSON body) and the
`config_change` lifecycle (kind="ids_policy"). No migration.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| `description` empty | template validation rejects (identity required) |
| `action`/`new_action` not in the enum | template validation rejects |
| `rulesets` filename bad charset | template validation rejects |
| Apply: referenced ruleset absent / not enabled on the device | `ApiError` "ruleset must be enabled first" → change `failed` with a clear message (no partial apply) |
| Apply: description resolves to >1 policy | `ApiError` (never mutate on doubt) — mirrors rule/test resolvers |
| `LIVE_PUSH_ENABLED` off | apply is a dry-run (preview only), like every push |
| delete of an absent policy | no-op success (`result: "absent"`) — idempotent (Revert-friendly) |

## Security

- Templates are tenant-scoped library entries; applying goes through the existing `CONFIG_PUSH` RBAC +
  CSRF + audit + per-device advisory lock + staleness guard + `LIVE_PUSH_ENABLED` master switch — no new
  authz surface. The connector adds only IDS policy endpoints behind the same SSRF-guarded HTTP boundary.
- All embedded path segments (policy uuid, ruleset filename) are charset-validated before URL use
  (anti path-injection), consistent with `apply_ids_rulesets`/`apply_grid_item`.
- No secrets are read or written by this kind.

## Testing

**Backend (pytest, fake client — mirrors `test_monit_kind.py` / `test_firewall_rule_kind.py`):**
- `ids_policy` kind registered; `to_change` → `("set", description, body)`; `pinned == ("description",)`.
- `_validate` accepts a good policy; rejects: empty description, bad enabled, non-int prio, bad action
  member, bad new_action, non-filename ruleset, malformed content.
- Applier dispatches `apply_ids_policy` with `(operation, payload, dry_run)`.
- Connector `apply_ids_policy` against a fake `_post`/`_get`/`list_ids_rulesets`: dry-run mutates nothing
  and returns the summary; `set` path posts `{"policy": …}` to addPolicy when absent / setPolicy/{uuid}
  when present (resolved by description) then reconfigure; `action` comma-joined; `rulesets` filenames
  resolved to enabled file-uuids (absent/disabled → `ApiError`); `delete` → delPolicy/{uuid}; >1
  description match → `ApiError`; bad uuid/filename → `ApiError` (charset guard).

**Frontend (Vitest; `npm run build` is the gate):**
- `IdsPolicyForm` renders the fields; selecting kind `ids_policy` in `TemplateFormModal` shows it and a
  save posts `{kind:"ids_policy", body:{…}}` (mock the create hook).
- i18n key parity holds (the build fails otherwise).

## Build phases (informs the plan)

- **PR1 — Backend** (`ids_policy` kind + connector add/set/delete + resolvers + main/worker imports +
  tests). Ships independently; the kind is usable via the API.
- **PR2 — Frontend** (`IdsPolicyForm` + `TemplateFormModal` wiring + 12-locale i18n + tests + build).

## Out of scope / future

- IDS policy **revert** (inverse builder) — handled by the paused Revert milestone
  ([[revert-inverse-builders-design]]); the connector's `delete` op here makes it trivial.
- A richer device-aware `content` metadata picker (the v1 editor is a raw key→values form; the available
  values are device/state-dependent and only populate once rulesets are downloaded).
- Auto-enabling a referenced ruleset as a side effect (v1 requires it to be enabled first, by design —
  pair with a `suricata_ruleset` template/profile).
