# Config Templates M3 — Curated kind: `monit_test` (Monit health-check tests)

**Status:** Design (curated-kinds program, "tutti i curati in sequenza" — 3rd/last curated kind).
**Date:** 2026-06-11

## Goal

Ship the curated `monit_test` config-template kind: a **portable Monit test** (a reusable
health-check condition — e.g. "cpu usage is greater than 90% → alert", "memory usage is greater
than 80% → alert"). An MSP keeps a standard monitoring-test library and pushes it fleet-wide so
every firewall reports on the same thresholds. Like the other curated kinds it is value-controlled
(type/action picked from the device's model) and **fleet-portable** (a test is a condition, not a
device object).

## Why `monit_test` (not `monit_service`)

Monit's model is `{general, alert, service, test}`. **Tests** define conditions; **services** are
monitored entities that reference tests **by UUID** and carry device-specific fields (`pidfile`,
`path`, `interface`, `address`). Service `tests`/`depends` are per-device UUIDs → **not portable**.
Tests (`SystemResource`/`ProcessResource`/… conditions) carry no device identifiers → **portable**.
Honouring the user's fleet-portability constraint, the curated kind is the **test**. (A monit test
takes effect once attached to a service; the form notes this. The default OPNsense `system` service
carries the SystemResource tests. Auto-attaching a pushed test to a service is a clean follow-up.)

## Verified OPNsense API (real box 26.1.9 — read + revertible write)

- `GET monit/settings/getTest` → `{"test": {name, type, condition, action, path}}` (blank model):
  `type` option-object (23 opts: Existence, SystemResource, ProcessResource, FileChecksum, …),
  `action` option-object (6 opts: alert, restart, start, stop, exec, unmonitor), `name`/`condition`/
  `path` plain strings.
- `GET monit/settings/searchTest` → `{rows:[{uuid, name, type, condition, action, path}]}`.
- `POST monit/settings/addTest {"test": {…}}` → `{"result":"saved","uuid":…}`.
- `POST monit/settings/setTest/{uuid} {"test": {…}}` → `{"result":"saved"}`.
- `POST monit/settings/delTest/{uuid}` → `{"result":"deleted"}`.
- `POST monit/service/reconfigure` → `{"status":"ok", …}`.
Verified live: added `SystemResource`/"cpu usage is greater than 95%"/alert test, confirmed via
search, deleted + reconfigured, confirmed gone — no residue.

## Architecture (reuses the kind engine + introspection; NO engine changes)

Closely mirrors `firewall_rule` minus the apply-time binding (a test has no interface — fully
portable).

### Template body (introspection-driven; all fields portable)
`{name, type, condition, action, path}` — flat dict. `name` is the identity (required).

### `monit_test` template kind (`app/services/monit_kind.py`)
- **validate:** `name` non-empty (identity); `action ∈ {alert,restart,start,stop,exec,unmonitor}`;
  `condition` non-empty; `type` non-empty. (Type's 23 options come from the value-controlled select;
  OPNsense `set`-validation is the final backstop.) `name` charset-safe is NOT required (it's a body
  field, not a URL path), but trimmed-non-empty is.
- **change_kind:** `"monit_test"`. **to_change:** `("set", name, body)`. **pinned:** `("name",)`.
- No `bind` (fully portable).

### `monit_test` applier (same module)
`_apply_monit_test(client, operation, payload, *, dry_run)` → `client.apply_monit_test(...)`.

### Connector (`app/connectors/opnsense/client.py`)
- `get_monit_test_model() -> dict`: GET `monit/settings/getTest`, return `["test"]`.
- `apply_monit_test(operation, payload, *, dry_run=True) -> dict`: upsert by `name` —
  `searchTest` exact-match (refuse on >1, never mutate on doubt; empty name → ApiError); 1 →
  `setTest/{uuid}`, 0 → `addTest`, body `{"test": payload}`; then `monit/service/reconfigure`.
  `dry_run` performs NO mutation.
- `_resolve_monit_test_uuid(name) -> str | None`.

### Read endpoint (`app/api/monit.py`)
`GET /api/tenants/{tid}/devices/{did}/opnsense/monit/test-model` → the introspected field schema
(`{"fields": [...]}`, reusing `setting_introspect` helpers on the flat `test` model).
`require_tenant(DEVICE_VIEW)`, cross-tenant device → 404, connector error → 502.

### Registration
Import `app.services.monit_kind` for side-effect in BOTH `main.py` and `worker.py`; include the new
router in `main.py`.

## Frontend (reuses `AutoFormFields`)
- `useMonitTestModel(deviceId)` hook (Load-triggered) → the test-model endpoint.
- `MonitTestForm.tsx`: reference-device → Load → `AutoFormFields` (testidPrefix `monit`) of the test
  fields → body `{name,type,condition,action,path}`. Requires `name`. Shows a note that a test takes
  effect once attached to a service.
- `TemplateFormModal`: add `monit_test` kind; render `MonitTestForm`; submit branch (client-side
  guard for required `name`). No apply-flow change (no bindings — fully portable).
- i18n under `templates.monit.*`. Regen API types after the endpoint lands.

## Testing
- Backend: validator (good; missing name; bad action; empty condition/type); connector
  `apply_monit_test` (dry-run no-call; add path; upsert/set path; ambiguous → refuse; reconfigure
  called) via respx; `get_monit_test_model` parse; read endpoint (shape, cross-tenant 404, 502, RBAC).
- Frontend: `MonitTestForm` (load → auto-form → onChange), modal branch + required-name guard.
- **Live verify** (revertible): create a `monit_test` template (a SystemResource alert test), apply
  to the box, confirm via searchTest, re-apply (upsert — no duplicate), then delete + reconfigure.

## Out of scope
- Monit services/alerts/general settings (services aren't portable); auto-attaching a pushed test to
  a service (follow-up). The generic `opnsense_setting` kind already covers monit *general* settings.
