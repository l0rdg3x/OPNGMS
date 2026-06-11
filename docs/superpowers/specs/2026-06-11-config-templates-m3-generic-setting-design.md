# Configuration Templates — M3: generic introspection-driven "OPNsense setting" kind — Design

**Status:** approved direction (2026-06-11). Reorganizes M3 from "curated kinds only" to **a generic, value-controlled "create your own template for (almost) any OPNsense setting" kind + built-in curated kinds**. This spec covers the GENERIC kind (the headline); the curated kinds (IDS rulesets, firewall rules, monit) are separate follow-on milestones, shipped built-in.

## 1. Goal

Let a user define a template for an OPNsense **model-based setting endpoint** (IDS general, Unbound, DHCP, …) through a UI form whose fields are **auto-generated and value-controlled by introspecting the endpoint's own `get` response** — without OPNGMS hand-coding each field. The template is applied to a device via the endpoint's `set` + `reconfigure`, inside the existing config-push pipeline (preview → now/scheduled → snapshot rollback). Built on the M3a kind-pluggable engine.

## 2. Key decisions

1. **Curated endpoint catalog, not free-form paths.** OPNGMS ships a vetted `SETTING_ENDPOINTS` catalog — each entry is `{key, label, get_path, set_path, reconfigure_path, model_root, multi_fields}`. The user picks an endpoint from this list; OPNGMS never writes to an arbitrary path. Adding an endpoint is a data-only change. (Why: correct get/set/reconfigure triples — `reconfigure` often lives on a different controller, e.g. `ids/service/reconfigure` vs `ids/settings/set`; and to prevent applying to a wrong/dangerous endpoint.)
2. **Introspection drives the FORM; the catalog drives WHICH endpoints.** The field controls (Select/MultiSelect/Switch/Text) are inferred from the endpoint's `GET get_path` response shape — so we don't hand-code fields. The catalog supplies the safe endpoint triple + a `multi_fields` hint for ambiguous multi-selects.
3. **Reference-device introspection at edit time.** The library is global/device-independent, but the model structure (available interfaces, option lists) is device-specific. So building an `opnsense_setting` template requires picking a **reference device** to read `get` from; the resulting payload is then applied to target devices. *Caveat (documented in the UI):* device-specific option values (e.g. an interface name) may not exist on every target — the server-side `set` validation is the backstop.
4. **Heuristic field inference + server-side validation backstop.** Inference covers the common shapes precisely; unknowns fall back to a text input; the ultimate validation is OPNsense's own `set` (its validation errors are surfaced). "Value control" = strong on options/booleans + server-validated, not a hand-crafted per-field schema.
5. **Reuse the M3a registries + config-push.** A new template kind `opnsense_setting` (TEMPLATE_KINDS) → a `config_change` kind `opnsense_setting` (CHANGE_APPLIERS) → a new connector write (`apply_setting`). No new tables, no new pipeline.

## 3. Field-inference rules (from a `GET get_path` response)

Walk `response[model_root]` (e.g. `response["ids"]`). For each leaf field:
- **option-dict** — a dict whose values are objects shaped `{ "value": <label>, "selected": 0|1 }`:
  - If **≥2** options are `selected:1` **OR** the field name is in the catalog's `multi_fields` → **MultiSelect** (options = the keys, labels = `.value`, value = the selected keys).
  - Else → **Select** (single; value = the one selected key, or "").
- **boolean** — the value is exactly `"0"` or `"1"` → **Switch** (on = "1").
- **string** — a plain string → **TextInput** (value = the string).
- **nested object** (not option-shaped, not a leaf) → recurse, rendering a grouped sub-section (dotted field path `parent.child`).
- anything else (lists, unknown) → a read-only/text fallback, flagged "advanced".

The inference produces a **field schema**: `list[{ path, label, control, options?, value }]`. The form renders controls from it; on save, it collects `{ path: value }` (selected key(s) for options, "0"/"1" for switches, string for text).

## 4. Architecture

### 4.1 Endpoint catalog (backend)

`app/connectors/opnsense/setting_endpoints.py`: `SETTING_ENDPOINTS: dict[str, SettingEndpoint]`. M3-gen ships ONE entry (the proof):
```
"ids_general": SettingEndpoint(
    key="ids_general", label="IDS — General settings",
    get_path="ids/settings/get", set_path="ids/settings/set",
    reconfigure_path="ids/service/reconfigure", model_root="ids",
    multi_fields=("general.interfaces", "general.homenet"),
)
```
Adding Unbound/DHCP/… later = appending entries (data-only).

### 4.2 Connector (introspect + apply)

- `OpnsenseClient.get_setting(get_path) -> dict` — the raw `get` response (for field inference). Goes through the SSRF-guarded `_get`.
- `OpnsenseClient.apply_setting(set_path, reconfigure_path, model_root, payload, *, dry_run) -> dict` — `dry_run` returns a summary (no write); else `POST set_path` with `{ model_root: payload }`, then `POST reconfigure_path`. (`set` expects the model values under the model root; option fields are set by their selected key(s) — comma-joined for multi.)

### 4.3 Template kind `opnsense_setting`

- **Body** (the template's `config_templates.body`): `{ "endpoint_key": "ids_general", "payload": { "<field.path>": <value>, ... } }`. `payload` keys are the dotted field paths from the inference; values are selected key(s)/"0"|"1"/string.
- **Validator:** `endpoint_key` in `SETTING_ENDPOINTS`; `payload` is a dict (its field values are not re-typed here — OPNsense validates on `set`).
- **Registry (`register_template_kind("opnsense_setting", ...)`):** `change_kind="opnsense_setting"`, `to_change(body) -> ("set", body["endpoint_key"], body)` (payload carries the endpoint_key + payload), `pinned=("endpoint_key",)` (an override may tweak `payload` but not repoint the endpoint).
- **Applier (`register_change_applier("opnsense_setting", ...)`):** reads `endpoint_key` + `payload` from the change payload, looks up the catalog entry, calls `client.apply_setting(set_path, reconfigure_path, model_root, payload, dry_run=...)`. The payload's option values are written under the model root nested by the dotted paths (the applier un-flattens `general.interfaces` → `{general: {interfaces: ...}}`).

### 4.4 API

- `GET /api/opnsense/setting-endpoints` (any-auth) → the catalog (keys + labels) for the kind picker.
- `GET /api/tenants/{tid}/devices/{did}/opnsense/settings/{endpoint_key}` (`require_tenant(DEVICE_VIEW)`) → introspect: read the device's `get_path`, return the inferred **field schema** (+ current values). Powers the form. (Tenant-scoped: reads from the tenant's own device.)
- The existing `opnsense_setting` template CRUD + per-device preview/apply go through the **M1/M2 template + profile API unchanged** (the kind is just data) — a profile can even bundle an `opnsense_setting` template with a `firewall_alias` one.

### 4.5 Frontend

In the superadmin Template form, the **kind selector** gains "OPNsense setting". Choosing it shows: an endpoint Select (from the catalog) + a **reference-device** Select → on pick, call the introspection API → render the **auto-generated form** (Select/MultiSelect/Switch/Text from the field schema). Save → `body = {endpoint_key, payload}`. The per-device apply/preview reuse the existing template apply UI (preview shows the payload; apply does set+reconfigure).

## 5. Data flow

Superadmin creates an `opnsense_setting` template: pick "IDS — General settings" + a reference device → OPNGMS introspects → a validated form (mode Select, interfaces MultiSelect, "enabled" Switch, homenet MultiSelect, …) → save the chosen values as `payload`. A tenant operator applies it to a device → materialize a `config_change(kind="opnsense_setting", payload={endpoint_key, payload})` → the worker's applier POSTs `{ids: {general: {...}}}` to `ids/settings/set` + `ids/service/reconfigure`, behind the advisory lock + staleness guard + snapshot.

## 6. Error handling / security

- `endpoint_key` not in the catalog → 422 (no arbitrary-path writes; the catalog is the allowlist).
- Introspection on an unreachable device → 502 (sanitized).
- `set`/`reconfigure` connector failure → the existing config-push `failed` path (sanitized reason, snapshot retained).
- The field inference is best-effort; OPNsense `set` validation is the final gate (its error is surfaced as the change result).
- All paths in the catalog are static (no user input in the URL path) → no path-injection surface. The `payload` values reach OPNsense via the JSON body (validated by OPNsense).
- Superadmin-only template writes; tenant-scoped introspection/apply (unchanged from M1/M2).

## 7. Testing

- **Inference (unit, pure):** given representative `get`-shaped dicts (option-dict single, option-dict multi via `multi_fields`, ≥2-selected multi, "0"/"1" boolean, plain string, nested object), the inferrer emits the right field schema. Edge cases: empty selected, unknown shape → text fallback.
- **Connector (respx):** `get_setting` GETs the path; `apply_setting` POSTs `{model_root: un-flattened payload}` to set_path then reconfigure_path; dry_run writes nothing.
- **Kind registry:** `opnsense_setting` registered (validate, change_kind, applier); validate rejects an unknown endpoint_key; the un-flatten maps `general.x` → `{general:{x:...}}`.
- **API:** catalog list; introspection endpoint returns a field schema (respx-mocked device `get`); a template of this kind applies → enqueues `apply_config_change`.
- **Frontend:** the kind picker → endpoint+reference-device → introspected auto-form (MSW-mocked schema) renders the right controls; save sends `{endpoint_key, payload}`.
- **Live (dev script, not CI):** introspect `ids/settings/get` on the real 26.1.9 box; build a payload that flips ONE harmless IDS general field (e.g. a homenet entry or a benign toggle), apply it (set+reconfigure), confirm via re-`get`, then revert (guaranteed cleanup). Do NOT enable the IDS engine in a way that disrupts the box.

## 8. Milestone roadmap

- **M3-gen (this spec):** the generic `opnsense_setting` kind + introspection form, proven on **IDS general settings**. Adding more setting endpoints (Unbound, DHCP, …) is then data-only catalog entries.
- **M3-ids:** built-in curated **IDS rulesets** kind (a ruleset multi-select; write via the verified `toggleRuleset/{filename}/{enabled}` + reconfigure). Collection-style — better as a curated kind than via generic introspection.
- **M3-rules:** built-in curated **firewall rules** kind (os-firewall `addRule`/`setRule`/`apply`).
- **M3-monit:** built-in curated **monit** kind (os-monit).
- **Later:** richer inference (formal field types), declarative/diff modes, more catalog endpoints.

## 9. Honest limitations (so expectations match)

- The auto-form covers **model-based** settings reachable via a catalog `get`/`set` endpoint — a large but not total slice of OPNsense (legacy config.xml-only settings are out).
- Inference is **heuristic**: precise for option/boolean/string fields; ambiguous multi-selects need the `multi_fields` hint; truly unusual fields fall back to text. OPNsense's own `set` validation is the backstop.
- Device-specific option values (interfaces, rulesets) introspected from the reference device may differ on a target device; `set` validation surfaces a mismatch rather than silently mis-applying.
