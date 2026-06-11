# Config Templates M3 — Curated kind: `suricata_ruleset` (IDS rulesets)

**Status:** Design approved (rides on the M3 curated-kinds program — user: "Tutti i curati in sequenza").
**Date:** 2026-06-11

## Goal

Ship the first **curated** config-template kind: `suricata_ruleset`. A template captures a
**set of Suricata/IDS rulesets to enable** (e.g. an "abuse.ch + ET-open baseline" for a small
business). Applying the template enables those rulesets on a device and reloads the IDS engine.
This is the policy-by-company-size use case the user described ("liste da abilitare").

It is a *curated* kind: shipped built-in (not user-defined), value-controlled (the user picks
rulesets from the device's actual catalog — no free text), and **fleet-portable** (ruleset
filenames come from the rule providers, not from the hardware).

## Verified OPNsense API (real box 26.1.9, 192.168.1.82, read + revertible write)

- `GET ids/settings/listRulesets` → `{total, rowCount, current, rows: [...]}`. Each row:
  `{description, filename, documentation_url, documentation, modified_local, enabled}`.
  `enabled` is the string `"0"`/`"1"`. 68 rulesets present; **all 68 filenames** match
  `[A-Za-z0-9._-]+` (no slashes) — the safe charset for URL-path embedding.
- `POST ids/settings/toggleRuleset/{filename}/{0|1}` → `{status: "0"|"1"}` (echoes new enabled
  state). Verified: toggled `abuse.ch.feodotracker.rules` ON then OFF, no residue.
- `POST ids/service/reconfigure` → `{status: "OK"}`. Reloads Suricata with the saved config.

The `{filename}` segment is embedded in the URL path → **charset-validate** it
(`[A-Za-z0-9._-]+`, anti path-injection) exactly like the existing `_plugin_name` guard.

## Scope decisions

- **Enable-only / additive / non-destructive.** Applying a template enables the listed rulesets;
  it does **not** disable rulesets absent from the list. A template = "these rulesets should be ON".
  (YAGNI: no disable semantics in M3; matches the user's "liste da abilitare". Documented limitation.)
- **Fleet-portable.** The body holds ruleset *filenames* only. No device/hardware identifiers.
- **Value-controlled UI.** The form's multi-select is populated from the device's live
  `listRulesets` catalog (label = description, value = filename). No free-text filenames.

## Architecture (reuses the existing kind-pluggable engine)

Mirrors the `opnsense_setting` (M3-gen) wiring exactly — two registries seeded by import
side-effect in both `main.py` and `worker.py`:

### Template body
```json
{ "rulesets": ["abuse.ch.feodotracker.rules", "abuse.ch.urlhaus.rules"] }
```

### `suricata_ruleset` template kind (`app/services/ids_kind.py`)
- **validate:** `rulesets` is a non-empty `list[str]`; every entry matches `[A-Za-z0-9._-]+`.
- **change_kind:** `"ids_rulesets"`.
- **to_change:** `lambda body: ("set", "ids_rulesets", body)` (target is a static descriptive label;
  payload = the whole body). Preview is already kind-aware via `TEMPLATE_KINDS[kind].to_change`.
- **pinned:** `()` — no identity field; a per-tenant override replaces the whole `rulesets` list.

### `ids_rulesets` config-change applier (same module)
`_apply_ids_rulesets(client, operation, payload, *, dry_run)` → `client.apply_ids_rulesets(...)`.

### Connector (`app/connectors/opnsense/client.py`)
- `list_ids_rulesets() -> list[dict]`: GET `ids/settings/listRulesets`, return `rows`.
- `apply_ids_rulesets(operation, payload, *, dry_run=True) -> dict`:
  - `dry_run=True` (default): NO mutation; return `{"dry_run": True, "rulesets": [...]}`.
  - else: for each filename in `payload["rulesets"]`, POST
    `ids/settings/toggleRuleset/{validated_filename}/1`; then POST `ids/service/reconfigure`
    (long timeout). Return `{"dry_run": False, "enabled": [...]}`.
- `_ruleset_filename(name)` static charset guard (`ApiError` on violation), reusing a module
  regex like `_PLUGIN_NAME_RE`.

### Read endpoint for the form (`app/api/ids.py`, new router)
`GET /api/tenants/{tid}/devices/{did}/opnsense/ids/rulesets`
- Auth: `require_tenant(Action.DEVICE_VIEW)`; cross-tenant device → 404 (mirror `introspect_setting`).
- Builds the client from the decrypted device creds, calls `list_ids_rulesets`, returns
  `[{filename, description, enabled}]` (drop the documentation HTML — only what the form needs).
- Connector `OpnsenseError` → 502.

## Frontend (mirrors `OpnsenseSettingForm`)
- `useIdsRulesets(deviceId)` hook (mutation, Load-button-triggered like `useIntrospectSetting`).
- `IdsRulesetForm.tsx`: reference-device Select → **Load** → `MultiSelect` of rulesets
  (value = filename, label = description). Selected → body `{rulesets: [...]}`. Controlled
  `{value, onChange}` like the setting form.
- `TemplateFormModal`: add `suricata_ruleset` to the kind Select; conditional render
  `IdsRulesetForm`; submit branch creates/updates with `kind: "suricata_ruleset"`, body `{rulesets}`.
- i18n strings under `templates.ids.*`.
- Regen API types (`npm run gen:api`) after the read endpoint lands.

## Testing
- Backend unit: validator (good list; empty/non-list/bad-charset rejected); connector
  `apply_ids_rulesets` (dry-run no-call; real path toggles each + reconfigure; bad filename →
  ApiError) via respx; `list_ids_rulesets` parse; read endpoint (200 shape, cross-tenant 404,
  502 on connector error, RBAC).
- Frontend: `IdsRulesetForm` (load populates multi-select; selection drives onChange) with MSW;
  `TemplateFormModal` suricata_ruleset branch.
- **Live verify** on the box: create a template enabling one currently-disabled ruleset, apply to
  the device, confirm `enabled=1`, then revert (toggle OFF) — fully revertible.

## Out of scope
- Disabling rulesets, per-rule (sid) policy, ruleset *download/update* scheduling, custom user
  rule files. (Future curated kinds / later milestones.)
