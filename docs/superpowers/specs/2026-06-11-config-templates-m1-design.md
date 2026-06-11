# Configuration Templates — M1: Engine + `firewall_alias` kind — Design

**Status:** approved design (brainstormed 2026-06-11). This is the FIRST milestone of a multi-milestone program.

## 1. Goal

Stand up a **configuration-template engine** that lets the MSP define reusable, typed templates in a **shared library**, let each customer apply a **per-tenant override** on top, and **apply** the resulting config to a device — now or scheduled, with a redacted preview and the existing rollback snapshot. M1 proves the whole vertical end-to-end on the lowest-risk kind, **firewall aliases**, reusing the verified `apply_alias` write and the M3 config-push pipeline. Profiles, additional kinds, and drift detection are later milestones.

## 2. Decisions (from brainstorming)

1. **Two layers** — per-subsystem *templates* (building blocks) + company *profiles* (bundles). **M1 builds templates only; profiles are M2.**
2. **Typed per-kind apply** with a redacted preview, translating to the connector writes appropriate to each device's `(edition, version)` — exactly like config-push's `alias` kind.
3. **Shared MSP library + per-tenant override** — a global library managed by the **superadmin**, with a per-customer override layer.
4. **First kind = `firewall_alias`** — reuse the already-HW-verified `apply_alias` write; M1's risk is in the *engine*, not a new connector surface.

## 3. Scope

**In scope (M1):**
- Global library table `config_templates` (kind `firewall_alias`), superadmin-managed (CRUD).
- Per-tenant override table `template_overrides` (a JSON merge-patch over a template body), tenant-scoped (RLS).
- A typed `firewall_alias` body schema + validation.
- "Apply template to device": resolve the effective body (`base ⊕ override`), **materialize a `config_change` of kind `alias`**, and run it through the existing config-push pipeline (preview → now/scheduled push → snapshot rollback point). Tag the change with its source template.
- Backend API + frontend UI: a superadmin **Template Library** page (alias-template CRUD) and a per-tenant **Apply template** flow on the device (reusing the device-actions preview + now/scheduled UI).

**Out of scope (later milestones):**
- **Profiles** (named bundles of templates = company tiers) → **M2**.
- **Additional kinds** (Suricata/IDS, firewall rules, monit, …) → **M3+**, each a typed schema + a HW-verified connector write + preview.
- **Drift detection** (does a device still match its applied template?) → later.
- A **raw/advanced** escape hatch → only if a future kind needs it.

## 4. Architecture

### 4.1 Data model

- **`config_templates`** — the global MSP library. NOT tenant-scoped (no tenant policy; superadmin-managed).
  - `id` (uuid pk), `kind` (str; M1 only `"firewall_alias"`), `name` (str, unique per kind), `description` (str, default ""), `body` (JSONB — typed per kind), `version` (int, default 1; bumped on edit), `created_by` (uuid — the superadmin user), `created_at`, `updated_at`.
- **`template_overrides`** — per-tenant customization. Tenant-scoped (RLS, ENABLE+FORCE, `tenant_isolation` policy, registered in `TENANT_TABLES`).
  - `id` (uuid pk), `template_id` (uuid FK → `config_templates.id`, ON DELETE CASCADE), `tenant_id` (uuid), `body_patch` (JSONB — a shallow merge-patch over the template body; `{}` = no override), `created_at`, `updated_at`. Unique `(template_id, tenant_id)`.
- **`config_changes.source_template_id`** — a new nullable `uuid` column (FK → `config_templates.id`, ON DELETE SET NULL) tagging a change materialized from a template. Minimal tracking; reuses the existing change/audit machinery rather than a new applications table.

**`firewall_alias` body schema** (typed, validated): `{ "name": str, "type": "host"|"network"|"port"|"url"|..., "content": list[str], "description": str }` — mirrors the alias payload the config-push `alias` kind already accepts (`{name, content, …}`). The override `body_patch` may override `content` and `description` (not `name`/`type`, which identify the alias).

### 4.2 Effective body

`effective(template, tenant) = merge(template.body, override.body_patch)` — a shallow per-key merge (patch keys win). Computed server-side at apply time and at preview time. No override → the base body.

### 4.3 Access model

- New **org-level** action `Action.TEMPLATE_MANAGE` added to `_ORG_ACTIONS` (superadmin only). Gates create/update/delete on `config_templates`.
- **Reading** the library: any authenticated tenant user may LIST/GET templates (needed to apply). Because `config_templates` is global, it has **no tenant RLS policy**; read is permissive and write is enforced at the API/RBAC layer (superadmin-only). This is the one deliberate departure from the otherwise strict per-tenant RLS — called out explicitly in §7.
- **Overrides + apply**: tenant-scoped, gated by `Action.CONFIG_PUSH` (same as a manual alias push). A tenant user edits only their own `template_overrides` (RLS-enforced) and applies to their own devices (the existing `_device_or_404` + config-push path).

### 4.4 Apply pipeline (reuse M3 + device-actions)

`POST .../devices/{id}/templates/{template_id}/apply` (body: `{ scheduled_at? }`, gated `CONFIG_PUSH`, CSRF):
1. Load the template (global) + the tenant's override → compute the effective `firewall_alias` body.
2. **Materialize** a `config_change`: `kind="alias"`, `operation="set"`, `target=<alias name>`, `payload=<effective body>`, `source_template_id=<template_id>`, `created_by=ctx.user.id`, `status="scheduled"`, `scheduled_at`.
3. Reuse the existing config-push **preview** (redacted summary) and the existing **enqueue → worker → apply_change** path (advisory lock, staleness guard, pre-apply snapshot rollback point). No new connector code — `apply_alias` does the write.

`POST .../devices/{id}/templates/{template_id}/preview` returns the redacted diff/summary for the effective body (reusing the config-push preview machinery) WITHOUT creating a change.

### 4.5 Components

- **Backend:** `models/config_template.py`, `models/template_override.py`; migration (new tables + RLS for overrides + `config_changes.source_template_id` column + `TENANT_TABLES` registration of `template_overrides`); `services/templates.py` (effective-body merge, validation, materialize-to-config_change); `schemas/templates.py`; `api/templates.py` (superadmin library CRUD + tenant override + apply/preview); RBAC `TEMPLATE_MANAGE`.
- **Frontend:** a superadmin **Template Library** page (list + alias-template editor: name/type/content/description); on the device, an **Apply template** action (pick a library template → edit this customer's override → preview → apply now/scheduled), reusing the device-actions preview + scheduling UI.

## 5. Data flow

Superadmin defines a `firewall_alias` template in the library → a customer (tenant) optionally sets an override (`body_patch`) → a tenant operator opens a device, picks the template, previews the redacted effective body, and applies now or scheduled → the engine materializes a `config_change(kind=alias, source_template_id=…)` → the existing config-push worker pushes it via `apply_alias` behind the advisory lock with a snapshot rollback point → the change's audit/result reflects the outcome.

## 6. Error handling

- Invalid `firewall_alias` body (missing `name`, empty `content`, bad `type`) → 422 at template create/update or at apply-time validation.
- Applying a template whose override produces an invalid effective body → 422 with the validation reason (no change created).
- Non-superadmin attempting library write → 403 (`TEMPLATE_MANAGE`).
- Cross-tenant override/device access → 404/403 (RLS + `_device_or_404`).
- Connector/push failures → handled by the existing config-push pipeline (status `failed`, sanitized reason, snapshot retained).
- Deleting a library template with existing overrides/applied changes → overrides CASCADE-delete; `config_changes.source_template_id` SET NULL (history preserved).

## 7. Risks / things to get right

- **Global (non-tenant) library vs strict RLS:** `config_templates` has no tenant policy — this is new. Reads are permissive to all authenticated tenant users; writes are superadmin-only enforced at the API/RBAC layer. A static guard test must assert the library is NOT in `TENANT_TABLES` (it must NOT get a tenant policy) while `template_overrides` IS. Verify a tenant user cannot create/edit/delete a library template.
- **Override merge semantics:** shallow per-key patch; document that only `content`/`description` are override-eligible and `name`/`type` are pinned (they identify the alias).
- **Reuse, don't fork:** apply must go through the *existing* config-push pipeline (materialize a `config_change`), not a parallel path — so the advisory lock, staleness guard, snapshot rollback, preview redaction, and audit all come for free.

## 8. Testing

- **Models/migration:** tables created; `template_overrides` RLS enforced (cross-tenant isolation test, like `firmware_actions`); `config_templates` is NOT in `TENANT_TABLES` (static guard) and has no tenant policy; `config_changes.source_template_id` column + FK.
- **Service:** effective-body merge (base only; base ⊕ patch; patch overrides `content`/`description`); `firewall_alias` validation; materialize produces a correct `config_change(kind=alias, target, payload, source_template_id)`.
- **API:** superadmin can CRUD library templates; non-superadmin gets 403 on writes but can LIST/GET; a tenant operator can set an override (only their own) and apply to their own device (enqueues a config_change); cross-tenant override/device → 404/403; invalid body → 422.
- **Frontend:** library CRUD page (superadmin); the apply flow (pick → override → preview → apply now/scheduled) reusing the device-actions UI; MSW-stubbed.
- **Live (dev script, not CI):** apply a throwaway `firewall_alias` template to the real OPNsense 26.1.9 box via the engine and confirm the alias lands (reusing the verified alias write), then clean up — proves the engine materialization drives the real write.

## 9. Milestone roadmap (context)

- **M1 (this spec):** engine + `firewall_alias` kind (library + per-tenant override + typed apply via config-push).
- **M2:** profiles (named bundles of templates → company tiers, applied as a unit, per-tenant assignment).
- **M3+:** new kinds — Suricata/IDS (rulesets/lists/policy), firewall rules, monit, … — each a typed schema + a HW-verified connector write + preview.
- **Later:** drift detection (device vs applied template) and a raw/advanced escape hatch if a kind needs it.
