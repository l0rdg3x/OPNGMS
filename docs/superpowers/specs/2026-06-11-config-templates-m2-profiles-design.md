# Configuration Templates — M2: Profiles — Design

**Status:** approved design (2026-06-11). SECOND milestone of the configuration-templates program (M1 = engine + `firewall_alias` kind, merged).

## 1. Goal

Let the MSP group library templates into named **profiles** (company tiers — "Small", "Medium", …) and **apply a whole profile to a device in one shot** (now or scheduled). A profile is a thin, ordered bundle that fans out to the existing M1 per-template apply — reusing per-tenant overrides, the config-push pipeline, preview, and the snapshot rollback. No new tenant-scoped data, no new connector code.

## 2. Decisions (from the M1 brainstorm + the M2 design review)

1. **Profile = an ordered bundle of library templates.** Global MSP library object (like `config_templates`), superadmin-managed.
2. **Apply = fan-out.** Applying a profile materializes **one `config_change` per member template, in order**, each with the effective body `template ⊕ that tenant's per-template override`, tagged with both `source_template_id` and `source_profile_id`, enqueued together (now or scheduled) through the existing `apply_config_change` worker. **No cross-template atomicity** (matches the per-device-action model): a member failing doesn't roll back the others; the UI shows the resulting set of changes.
3. **No profile-level override.** Reuse M1's per-template `template_overrides` (tenant-scoped). A profile inherits each member's per-tenant override automatically.
4. **Ad-hoc apply.** "Apply this profile to this device" (now/scheduled). A persistent device→profile **assignment + drift detection** is deferred (like M1's drift).
5. **RBAC:** profile CRUD = **superadmin** (reuse `Action.TEMPLATE_MANAGE` — profiles are part of the same library). Apply = tenant `Action.CONFIG_PUSH`.

## 3. Scope

**In scope (M2):**
- `config_profiles` (global MSP library) + `config_profile_members` (ordered join → templates) + `config_changes.source_profile_id` tag.
- Backend: profile CRUD (superadmin), member management (set the ordered template list), any-auth LIST; per-device **profile preview** (the list of per-template effective previews) and **profile apply** (fan-out to N config_changes).
- Frontend: profiles in the superadmin library UI (create/edit a profile = name + an ordered pick-list of templates); a per-device "apply profile" affordance (pick a profile → preview the member set → apply now/scheduled), in the existing Templates tab.

**Out of scope (later):**
- Profile-level overrides (reuse per-template).
- Persistent assignment + drift (device "has" a profile).
- Cross-template transactional atomicity / automatic rollback of a partial profile apply.
- New template kinds (Suricata/rules/monit) — that's **M3+**; M2 works with whatever kinds exist (M1: `firewall_alias`).

## 4. Architecture

### 4.1 Data model

- **`config_profiles`** — global MSP library (NOT tenant-scoped, no RLS policy; superadmin-write at the API layer; same posture as `config_templates`).
  - `id`, `name` (unique), `description` (default ""), `version` (int, default 1), `created_by`, `created_at`, `updated_at`.
- **`config_profile_members`** — the ordered bundle (also global — which templates a profile contains is MSP-defined, not per-tenant).
  - `id`, `profile_id` (FK → `config_profiles.id`, CASCADE), `template_id` (FK → `config_templates.id`, CASCADE), `position` (int). Unique `(profile_id, template_id)`. Ordered by `position`.
- **`config_changes.source_profile_id`** — new nullable `uuid` FK → `config_profiles.id` (ON DELETE SET NULL), tagging a change materialized as part of a profile apply (alongside the M1 `source_template_id`).

No new tenant-scoped table: profiles + members are global; per-tenant customization stays in M1's `template_overrides`; the produced `config_changes` are tenant-scoped as today.

### 4.2 Apply pipeline

`POST .../devices/{id}/profiles/{profile_id}/apply` (body `{scheduled_at?}`, `CONFIG_PUSH`, CSRF):
1. Load the profile + its ordered members (template ids in `position` order).
2. For each member template, in order: compute the effective body (`template.body ⊕ this tenant's override`, reusing the M1 `templates` service), **materialize a `config_change`** (kind `alias`, op `set`, `source_template_id` + `source_profile_id`), `status="scheduled"`, `scheduled_at`.
3. Commit all the changes, then enqueue `apply_config_change` for each (deferred to `scheduled_at`). The existing worker applies each behind the per-device advisory lock + staleness guard + snapshot.
4. Return the list of created `change_id`s.

`POST .../devices/{id}/profiles/{profile_id}/preview` returns an ordered list of per-template `TemplatePreviewOut` (the effective body of each member) — no changes created.

### 4.3 Access / RBAC

Same as M1: `config_profiles`/`config_profile_members` are global, read by any authenticated user (to apply), written only by the superadmin (`require_org(TEMPLATE_MANAGE)`). Apply/preview are `require_tenant(CONFIG_PUSH)` (tenant GUC set; the per-template overrides read under RLS). `_device_or_404` blocks cross-tenant devices.

## 5. Components

- **Backend:** `models/config_profile.py` (+ member model), migration (two global tables + `config_changes.source_profile_id`; NO new `TENANT_TABLES` entry), `services/profiles.py` (resolve members → fan-out materialize, reusing `services/templates.py`), `schemas/profiles.py`, `api/profiles.py` (superadmin CRUD + member-set; tenant preview/apply). Audit each operation (like M1).
- **Frontend:** profile CRUD in the library UI (name + ordered template multi-select); per-device "apply profile" (pick → preview the member set → apply now/scheduled), reusing the M1 apply modal.

## 6. Data flow

Superadmin defines templates (M1) and groups them into a profile (ordered) → a customer optionally sets per-template overrides (M1) → a tenant operator opens a device, picks a profile, previews the ordered member set, and applies now/scheduled → the engine fans out to one `config_change` per member (effective body, tagged with template + profile) → the existing worker applies each behind the lock/staleness/snapshot guards → the device's change list reflects the set.

## 7. Error handling

- Empty profile (no members) → apply is a 400/no-op with a clear message.
- A member template deleted after profile creation → the FK CASCADE removes the member row, so the profile simply has fewer members (no dangling).
- An invalid effective body for a member → that member's materialize raises 422; apply validates ALL members up front and fails the whole apply with 422 (no partial enqueue) — i.e. validate-all-before-enqueue, so a bad member doesn't leave a half-applied profile pending.
- Connector/push failures per member → handled by the existing pipeline (per-change `failed`, sanitized reason, snapshot retained). Cross-member partial success is possible and surfaced in the change list (documented non-atomicity).
- Non-superadmin profile write → 403; cross-tenant device/apply → 404/403.

## 8. Testing

- **Models/migration:** `config_profiles` + `config_profile_members` global (NOT in `TENANT_TABLES`, no policy — static guard); ordered members; `config_changes.source_profile_id` column + FK; CASCADE on profile/template delete.
- **Service:** ordered member resolution; fan-out materialize produces N `config_changes` in order with correct `source_template_id`/`source_profile_id` and per-tenant-override-applied bodies; validate-all-before-enqueue (a bad member → 422, zero changes).
- **API:** superadmin CRUD + member-set; non-superadmin 403; any-auth LIST; tenant preview (ordered member previews) + apply (enqueues N `apply_config_change`); empty profile → 400; cross-tenant → 404; audit rows.
- **Frontend:** profile CRUD (ordered template pick) in the library; per-device apply-profile (pick → preview member set → apply now/scheduled), MSW-stubbed.
- **Live (dev script, not CI):** build a 2-template profile, apply it to the real OPNsense 26.1.9 box via the engine, confirm both aliases land, clean up.

## 9. Milestone roadmap (context)

- **M1 (done):** engine + `firewall_alias` kind (library + per-tenant override + typed apply).
- **M2 (this spec):** profiles — ordered bundles of templates, fan-out apply.
- **M3+:** new kinds — Suricata/IDS, firewall rules, monit, … — each a typed schema + a HW-verified connector write + preview. Profiles + apply work unchanged across kinds.
- **Later:** persistent assignment + drift; profile-level overrides; cross-member atomicity.
