# Group-based RBAC — implementation plan

> Execute task-by-task; run backend tests after each backend task. Security-review before merge.
> Spec: `docs/superpowers/specs/2026-06-13-groups-rbac-design.md`.

**Goal:** Layer group-based RBAC over the existing `Membership` model. Users inherit tenant-scoped roles
from groups; effective role = highest of direct membership + covering group grants. Org/critical actions
stay superadmin-only. Backward compatible.

**Branch:** `feat/groups-rbac` (off main @ v0.3.0). Migration number: **0031** (head is 0030).

---

## Task 1 — Models (`backend/app/models/group.py`) + register

Three org-level (non-RLS) models, mirroring `Membership`/`config_templates` style:
- `Group(UUIDPKMixin, TimestampMixin)`: `name: str`, `description: str = ""`.
- `GroupMember(UUIDPKMixin)`: `group_id` FK groups CASCADE, `user_id` FK users CASCADE, UNIQUE(group_id,user_id).
- `GroupGrant(UUIDPKMixin)`: `group_id` FK groups CASCADE, `all_tenants: bool=False`, `tenant_id: uuid|None`
  FK tenants CASCADE, `role: str`. CHECK `(all_tenants AND tenant_id IS NULL) OR (NOT all_tenants AND tenant_id IS NOT NULL)`.
  Partial unique indexes: one wildcard per group; one per (group, tenant).
Register all three in `app/models/__init__.py` (`Base.metadata` + `__all__`).

## Task 2 — Migration `0031_groups_rbac.py`

`down_revision = "0030"`. `op.create_table` for the 3 tables + the CHECK + partial unique indexes
(`op.create_index(..., postgresql_where=...)`). Tables are NOT added to `TENANT_TABLES` (org-level, no RLS).
Reapply `grant_app_role_statements()` so `opngms_app` can read/write them (like 0028). `downgrade` drops them.

## Task 3 — RBAC (`backend/app/core/rbac.py`)

- Add `GROUP_MANAGE = "group.manage"` to `Action` and to `_ORG_ACTIONS` (superadmin-only).
- Add `ROLE_RANK = {READ_ONLY:1, OPERATOR:2, TENANT_ADMIN:3}` and `def highest_role(roles: Iterable[str|None]) -> str|None`
  (max by rank, ignoring None/unknown). `can()` is UNCHANGED.

## Task 4 — Resolver (`backend/app/core/access.py`, new)

`async def resolve_effective_role(session, *, user, tenant_id) -> str | None`:
- superadmin → return None here; callers treat `is_superadmin` separately (can() already handles it).
- collect roles: direct `Membership(user_id, tenant_id)` role (if any) + every `GroupGrant.role` for grants
  whose `group_id` is in the user's groups AND (`all_tenants` OR `tenant_id == tenant_id`).
- return `highest_role(roles)`.
Single query: join `group_members` → `group_grants` filtered by user_id + (all_tenants OR tenant_id=:tid),
UNION the membership role. Keep it one round-trip where practical.
Also `async def tenants_for_user(session, user) -> dict[tenant_id, role]` (direct memberships + group grants;
all_tenants grant ⇒ every tenant) for `/api/me/tenants`.

## Task 5 — `tenant_context` (`backend/app/core/deps.py`)

Replace the membership-only check: if not superadmin, `role = await resolve_effective_role(...)`; 403 only
when `role is None`. Still `await set_tenant_context(session, tenant_id)` unchanged. (RLS unchanged — a group
grant changes tenant ENTRY, never RLS scope.)

## Task 6 — `/api/me/tenants` (`backend/app/api/me_tenants.py`)

Non-superadmin branch: use `tenants_for_user`; return tenants reachable by direct membership OR group grant,
each with the effective (highest) role. Superadmin branch unchanged.

## Task 7 — Repositories + schemas

`backend/app/repositories/group.py`: GroupRepository (CRUD groups, set members, add/del grants, list with
member counts + grants). `backend/app/schemas/group.py`: GroupIn/Out, GroupMembersIn, GroupGrantIn/Out
(role constrained to the 3 tenant roles via validator; `all_tenants` xor `tenant_id`).

## Task 8 — Admin API (`backend/app/api/groups.py`, superadmin-only)

Mirror `app/api/users.py` (`require_org(Action.GROUP_MANAGE)`, `enforce_csrf`, AuditService on every mutation):
`GET /api/groups`, `POST`, `PATCH /{id}`, `DELETE /{id}`, `PUT /{id}/members`, `POST /{id}/grants`,
`DELETE /{id}/grants/{grant_id}`. Register the router in `app/main.py`.

## Task 9 — Backend tests

`tests/test_groups_rbac.py` + extend `tests/test_rls_isolation.py`:
- highest-of(direct, group, all-tenants); group-only user reaches a granted tenant at the granted role;
  no membership + no grant ⇒ 403; all_tenants ⇒ every tenant; read_only grant cannot write.
- **RLS:** a group member with an all_tenants grant, acting in tenant A, sees ONLY tenant A rows.
- `/api/me/tenants` union + effective roles.
- Admin API superadmin-only (403 for tenant_admin), round-trips, audited.

## Task 10 — Frontend (delegate to subagent once API exists)

`gen:api`; new superadmin-only "Groups" admin page (nav gated on `me.is_superadmin`): list/create/edit
groups, member multiselect, grant editor (all-tenants toggle OR tenant picker + role). New i18n keys across
12 locales. Tests. `npm run build` + lint + vitest.

## Task 11 — Security review

Run the `security-reviewer` agent on the diff. Verify: group reach widens tenant ENTRY not RLS scope; no
`_ORG_ACTIONS` grantable via group; `role` constrained to the 3 tenant roles (no superadmin injection);
resolution reads don't leak cross-tenant; read_only all-tenants stays read-only. Fix BLOCKER/IMPORTANT.

## Task 12 — PR → security-review green → squash-merge → tag.
