---
name: new-template-kind
description: Scaffold a new curated config-template kind for OPNGMS (connector + kind registry + read endpoint + introspection-driven form + modal wiring + tests + live box verification). Use when adding a templatable OPNsense config kind (e.g. NAT rule, schedule, DHCP). Follows the proven firewall_alias / opnsense_setting / suricata_ruleset / firewall_rule / monit_test pattern.
---

# Adding a curated config-template kind

OPNGMS templates are **kind-pluggable** via two registries seeded by import side-effect in BOTH `backend/app/main.py` and `backend/app/worker.py`:
- `TEMPLATE_KINDS` (`app/services/templates.py`): `register_template_kind(kind, TemplateKind(validate, change_kind, to_change, pinned, bind=None))`.
- `CHANGE_APPLIERS` (`app/services/config_apply.py`): `register_change_applier(change_kind, applier)`.

A template body is a flat dict; applying it materializes a `config_change` that the existing config-push pipeline executes (preview → now/scheduled → snapshot rollback). Preview/profiles are already kind-aware via `TEMPLATE_KINDS[kind].to_change` — no edits needed there.

## Hard rules (from the existing kinds)

- **Fleet-portable + value-controlled.** Exclude device-specific/hardware fields from the body. Per-device values (e.g. an interface) are **apply-time bindings** via the `bind(body, bindings)` hook + `ApplyTemplateIn.bindings`, never stored in the template. Option fields are picked from the device's live model (introspection), not free text.
- **Identity + idempotent upsert.** Pick an identity field (e.g. `name`/`description`). The connector resolves it via `search*` (EXACT match), `set*` if 1 match, `add*` if 0, **refuse (ApiError) if >1** — never mutate on doubt. Re-apply must not duplicate.
- **Anti path-injection.** Any value embedded in an OPNsense URL path is charset-validated (`[A-Za-z0-9._-]+`) BEFORE the request, in BOTH the connector and the kind validator (defense in depth).
- **dry_run mutates nothing.**

## Verify the API on the real box FIRST (read + revertible write)

Before writing code, confirm the endpoints on a real OPNsense box with an ephemeral `/tmp` probe (`verify_tls=False` for a throwaway local probe; load the API key/secret from your local credentials file — NEVER print them). Confirm the blank `get*` model (option-objects/strings), the `search*` shape, and `add*`/`set*`/`del*`/`reconfigure`. Do any mutation **revertibly** (add → confirm → delete/revert → confirm gone).

## Backend (PR 1) — mirror `app/services/monit_kind.py`, `app/api/ids.py`

1. **Connector** (`app/connectors/opnsense/client.py`): `get_<kind>_model()` (read for the form) + `apply_<kind>(operation, payload, *, dry_run)` (upsert + reconfigure; charset-guard URL-path values) + `_resolve_<kind>_uuid(identity)`.
2. **Introspection** (`app/services/<kind>_introspect.py`, optional): `infer_<kind>_fields(...)` reusing `setting_introspect`'s `_is_option_dict/_options/_selected`; exclude device-specific fields.
3. **Kind** (`app/services/<kind>_kind.py`): `_validate` (identity required + enums + charset), `register_template_kind(...)`, `_apply_<kind>` + `register_change_applier(...)`. Add `import app.services.<kind>_kind  # noqa: F401` to BOTH `main.py` and `worker.py`.
4. **Read endpoint** (`app/api/<kind>.py`): `GET /api/tenants/{tid}/devices/{did}/opnsense/<area>/<thing>` — `require_tenant(Action.DEVICE_VIEW)`, cross-tenant device → 404, `OpnsenseError → 502`. Include the router in `main.py`.
5. **Tests** (`tests/test_<kind>_*.py`): connector via respx (dry-run no-call, add path, upsert/set path, ambiguous → refuse), kind (validate good/bad + applier dispatch), introspect, api (200 shape + cross-tenant 404; copy the `_seed_members/_insert_device/_login` helpers from `tests/test_ids_api.py`).

Gate: `cd backend && TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test .venv/bin/python -m pytest -q` and `.venv/bin/ruff check app/` (CI lints `app/` only).

## Frontend (PR 2) — mirror `FirewallRuleForm.tsx` / `MonitTestForm.tsx`

6. **Hook** in `settingHooks.ts`: `use<Kind>Model(deviceId)` (Load-triggered mutation → the read endpoint).
7. **Form** `<Kind>Form.tsx`: reference-device → Load → `<AutoFormFields testidPrefix="<kind>" />` over the model fields; controlled `{value:{payload}, onChange}`; reuse exported `initialPayload`.
8. **Wire** `TemplateFormModal.tsx`: `<kind>Body` state, seed from `editing` on open, kind option, render branch, submit branch with a client-side required-identity guard.
9. **Tests** mirror `src/templates/__tests__/firewallRuleForm.test.tsx` + add a `templateFormModal.test.tsx` case.
10. **Regen** `npm run gen:api`; gate `npx vitest run && npm run lint && npx tsc --noEmit`.

## Finish

- **Live-verify** the full path through the connector code on the box (revertibly): apply → confirm → re-apply (upsert, no dup) → revert.
- **Update the README** templates row (keep it current per milestone).
- Two-stage review (spec compliance + the **security-reviewer** agent) before each PR. main is protected → PR with green checks.
