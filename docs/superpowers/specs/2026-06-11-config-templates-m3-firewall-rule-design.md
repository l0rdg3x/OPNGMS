# Config Templates M3 — Curated kind: `firewall_rule` (Rules [new] / MVC filter rules)

**Status:** Design approved (user: "permetti tutti i campi possibili … devi farlo per rules new"; interface = apply-time binding).
**Date:** 2026-06-11

## Goal

Ship the curated `firewall_rule` config-template kind: a **portable** firewall filter rule (action,
protocol, source/destination, ports, logging, state/advanced options) that an MSP keeps in the
library and applies to any device. The target **interface** is NOT stored in the template (interface
names differ per device); it is chosen **at apply time** from the device's live interface list
(empty = floating rule). The rule body exposes **all portable fields** of the rule model via an
introspection-driven, value-controlled auto-form (same philosophy as the generic `opnsense_setting`
kind), excluding only device-specific reference fields.

## Which rules — "Rules [new]" (MVC/API), NOT the legacy rules

OPNsense 26.1 has two firewall-rule systems: the **legacy** "Firewall → Rules" (static PHP, **no
API**) and the new **MVC "Rules [new]"** (the automation filter controller, promoted to the new
Rules GUI). We target **Rules [new]** via `/api/firewall/filter/*`. The blank model read from the
real box carries the new-rules fields (`state-policy`, `sequence`, `prio_group`, `sort_order`),
confirming this is the MVC system, not the legacy `<filter>` config. Refs: OPNsense core firewall
API docs; 26.1 release notes; Thomas-Krenn 26.1 Firewall Rule Migration.

## Verified OPNsense API (real box 26.1.9 — read + revertible write)

- `GET firewall/filter/getRule` (no uuid) → `{"rule": {<field>: <value>}}` — the blank model. Fields
  are option-objects `{key:{value,selected}}` (action, direction, ipprotocol, protocol(133),
  icmptype, statetype, state-policy, prio/set-prio, tos, tcpflags1/2, overload, interface(device's
  ifaces), gateway, sched, shaper1/2, replyto, divert-to), `"0"|"1"` strings (enabled, quick, log,
  *_not, allowopts, nosync, …), plain strings (sequence, source_net, destination_net, *_port,
  max-src-*, statetimeout, tag, description, …), and lists (`categories`).
- `POST firewall/filter/addRule {"rule": {...}}` → `{"result":"saved","uuid":...}`.
- `POST firewall/filter/setRule/{uuid} {"rule": {...}}` → `{"result":"saved"}`.
- `POST firewall/filter/delRule/{uuid}` → `{"result":"deleted"}`.
- `GET/POST firewall/filter/searchRule` (`searchPhrase`) → `{rows:[{uuid, description, interface, action, …}]}`.
- `POST firewall/filter/apply` → `{"status":"OK\n\n"}`.
Verified live: added a **disabled** floating block rule, confirmed via search, deleted + applied,
confirmed gone — no residue.

## Scope decisions

- **All portable fields exposed** (introspection-driven auto-form). EXCLUDED from the template body
  (device-specific references or computed): `interface` (→ apply-time binding), `gateway`,
  `replyto`, `divert-to`, `categories`, `sched`, `shaper1`, `shaper2`, `sort_order`, `prio_group`,
  and any `%`-prefixed display-mirror field (e.g. `%action`).
- **Interface = apply-time binding.** Stored nowhere in the template. At apply, the user picks an
  interface from the device's live list (empty = floating). Threaded via a new generic `bindings`
  channel on apply.
- **Identity = `description`** (required, non-empty). Re-apply is **idempotent**: upsert by
  `(description, interface)` — if exactly one matching rule exists, `setRule` it; if none, `addRule`;
  if multiple, refuse (ambiguous). Mirrors the alias resolve-by-name guard.
- **Value-controlled.** Option fields render as selects/multiselects from the device model; `"0"/"1"`
  as switches; free-form fields (`source_net`, `*_port`, `description`, numeric limits) as validated
  text. `source_net`/`destination_net` legitimately accept `any` | IP/CIDR | alias name (aliases
  compose with the `firewall_alias` kind). OPNsense's own `set`-validation is the final backstop.
- **Profiles (M3 limit):** a `firewall_rule` member applied via a **profile** uses no interface
  (floating), since profile apply doesn't (yet) carry an interface binding. Documented; per-interface
  profile apply is a clean follow-up. Direct template apply gets the interface picker.

## Architecture

### Engine extension — generic apply-time `bindings`
- `TemplateKind` gains `bind: Callable[[dict, dict], dict] | None = None` (default identity). Given
  the effective body and the apply `bindings`, returns the bound body. `firewall_rule.bind` injects
  `interface` from `bindings`.
- `ApplyTemplateIn` (schemas) gains `bindings: dict | None = None`.
- `materialize_change(..., bindings=None)`: if the kind has `bind`, `body = spec.bind(body, bindings or {})`
  BEFORE validate + `to_change`. So validation/preview see the bound interface.
- `api/templates.py` `apply_template` passes `body.bindings`; `preview_template` may accept an
  optional `bindings` query/body too (so the preview shows the chosen interface). Profiles pass no
  bindings (floating).

### `firewall_rule` template kind (`app/services/firewall_rule_kind.py`)
- **validate:** `description` non-empty; `action ∈ {pass,block,reject}`; `direction ∈ {in,out}`;
  `ipprotocol ∈ {inet,inet6,inet46}`; `source_net`/`destination_net` match `any|<IP/CIDR>|<alias>`;
  `source_port`/`destination_port` match `''|<port/range>|<alias>`; `interface` (if present from
  bind) safe charset or empty. Other fields pass through (OPNsense validates).
- **change_kind:** `"firewall_rule"`. **to_change:** `("set", body.get("description",""), body)`.
- **bind:** `lambda body, b: {**body, "interface": b.get("interface", "")}`.
- **pinned:** `("description",)` — identity; override may tweak other fields, not the identity.

### `firewall_rule` applier (same module)
`_apply_firewall_rule(client, operation, payload, *, dry_run)` → `client.apply_firewall_rule(...)`.

### Connector (`app/connectors/opnsense/client.py`)
- `get_firewall_rule_model() -> dict`: GET `firewall/filter/getRule`, return `["rule"]`.
- `apply_firewall_rule(operation, payload, *, dry_run=True) -> dict`:
  - `dry_run`: NO mutation; return `{"dry_run":True, "description":…, "interface":…}`.
  - else: resolve uuid by `(description, interface)` via `searchRule` (EXACT match on both; refuse on
    ambiguity, never mutate on doubt); `setRule/{uuid}` if found else `addRule`, body `{"rule": payload}`;
    then `firewall/filter/apply`. Return `{"dry_run":False, "operation":"set"|"add", "result":…}`.
  - Interface is validated against the device's real interfaces (from the rule model) or empty.

### Introspection (`app/services/firewall_introspect.py`)
`infer_rule_fields(get_rule_response) -> {"fields":[…], "interfaces":[{value,label}]}` — reuses the
`setting_introspect` classification helpers; walks the flat `rule` model; skips the EXCLUDED set and
`%`-fields; surfaces `interface`'s options as `interfaces` (for the apply picker), not as a field.

### Read endpoint (`app/api/firewall_rules.py`)
`GET /api/tenants/{tid}/devices/{did}/opnsense/firewall/rule-model` →
`{"fields":[…], "interfaces":[…]}`. `require_tenant(DEVICE_VIEW)`, cross-tenant device → 404,
connector error → 502. Powers the creation auto-form (`.fields`) and the apply interface picker
(`.interfaces`).

### Registration
Import `app.services.firewall_rule_kind` for side-effect in BOTH `main.py` and `worker.py`; include
the new router in `main.py`.

## Frontend
- `useFirewallRuleModel(deviceId)` hook (mutation, Load-triggered) → the rule-model endpoint.
- Extract a small presentational `AutoFormFields` from `OpnsenseSettingForm` (renders switch/select/
  multiselect/text from a `fields[]` + controlled `payload`), reused by both the setting form and the
  new `FirewallRuleForm`.
- `FirewallRuleForm.tsx`: reference-device → Load → the auto-form of portable rule fields → body
  `{<field>: <value>}`. Requires a `description`.
- `TemplateFormModal`: add `firewall_rule` kind; render `FirewallRuleForm`; submit branch.
- Apply flow (per-device Apply tab / ApplyTemplate): when the chosen template's kind is
  `firewall_rule`, show an **interface Select** (from the device's `rule-model.interfaces`, plus an
  empty "floating" option) and pass `{bindings:{interface}}` to preview + apply.
- i18n under `templates.fw.*`. Regen API types after the endpoint + the `bindings` field land.

## Testing
- Backend unit: validator (good rule; missing description; bad action/direction/ipprotocol; bad
  net/port); connector `apply_firewall_rule` (dry-run no-call; add path; upsert/set path; ambiguous →
  error; apply called) via respx; `get_firewall_rule_model` parse; `infer_rule_fields` (excludes
  device fields + `%`-fields, surfaces interfaces); read endpoint (shape, cross-tenant 404, 502, RBAC);
  engine `bind`/`bindings` (interface injected into the change; floating when absent).
- Frontend: `AutoFormFields` (unchanged setting behavior still green), `FirewallRuleForm`, modal
  branch, apply interface picker → bindings in the request.
- **Live verify** (revertible): create a `firewall_rule` template (disabled block rule), apply to the
  box on an interface, confirm via searchRule, re-apply (confirm upsert — no duplicate), then delete +
  apply to revert.

## Out of scope
- Legacy (non-API) rules; NAT rules; per-rule move/ordering UI; per-interface interface binding inside
  profiles (floating only there in M3); category/schedule/shaper/gateway references.
