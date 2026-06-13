# Group-based RBAC — design

**Date:** 2026-06-13 · **Status:** Approved (brainstorm Q&A) · **Security: tenant-isolation critical — security-review required before merge**

## Problem

OPNGMS is run by MSP teams. Today access is either:
- **`User.is_superadmin`** (a user flag) → absolute: all tenants **and** all org/critical settings, or
- a per-tenant **`Membership(user_id, tenant_id, role)`** → access to exactly one tenant.

For an MSP where a team of N people all work on **all** clients, both are wrong: superadmin grants far too
much (SMTP, system settings, tenant delete, global template library, user management), while per-tenant
memberships explode (N users × every client, re-synced on every new client). There is no "operate on all
tenants, but not the critical app settings" tier.

## Goal

Add **group-based RBAC layered over the existing `Membership` model** (fully backward compatible). A user's
access to a tenant is the **union (highest-privilege wins)** of their direct memberships and the grants of
every group they belong to. Groups grant **only tenant-scoped roles**; org/critical actions stay
**superadmin-only**.

## Approved decisions (from the brainstorm)

1. **Model = groups** (users → groups → tenant-scoped role grants; users inherit). Not a single platform flag.
2. **Grant scope = wildcard (ALL tenants) *and* specific tenants.** An all-tenants grant auto-covers future clients.
3. **Groups are strictly tenant-scoped.** They grant `tenant_admin | operator | read_only` only. Every
   org/critical action (`_ORG_ACTIONS`: tenant create/delete, user management, **group management**, SMTP,
   system settings, global template library management) remains **superadmin-only** and is NOT group-grantable.
4. **`superadmin` flag unchanged** (absolute). "MSP operator" is not a new flag — it is simply membership in
   a group that has an all-tenants `tenant_admin` (or `operator`) grant.
5. **Direct `Membership` preserved** for one-off scoped access (e.g. an external person on a single client).

## Data model

Three new **org-level (non-RLS, superadmin-managed)** tables. They are NOT tenant-scoped — they describe
*who may reach which tenants*, so they must be readable when resolving any request's tenant context. (The
owner/worker connection already bypasses RLS; the app role reads them via the resolution path — see Security.)

```
groups
  id            uuid pk
  name          text not null            -- e.g. "MSP Staff"
  description   text not null default ''
  created_at / updated_at

group_members
  group_id      uuid fk groups(id) on delete cascade
  user_id       uuid fk users(id)  on delete cascade
  UNIQUE (group_id, user_id)

group_grants
  id            uuid pk
  group_id      uuid fk groups(id) on delete cascade
  all_tenants   boolean not null default false   -- wildcard scope
  tenant_id     uuid null fk tenants(id) on delete cascade  -- set iff NOT all_tenants
  role          text not null                    -- 'tenant_admin' | 'operator' | 'read_only'
  CHECK ( (all_tenants AND tenant_id IS NULL) OR (NOT all_tenants AND tenant_id IS NOT NULL) )
  -- partial unique indexes: one wildcard grant per (group), one per (group, tenant_id)
```

A new migration creates the three tables (next sequential number — **0030** off current `main`; renumber to
0031 if `feat/report-enrichment`'s own 0030 lands first — set `down_revision` to whatever head is at build
time). No backfill — existing memberships keep working unchanged.

## Permission resolution

Add a single resolver used by `tenant_context` (and reused by `/api/me/tenants`):

```
effective_role(user, tenant_id) -> str | None
  if user.is_superadmin: return a sentinel handled by can() (superadmin path, unchanged)
  roles = []
  m = Membership(user, tenant_id);  if m: roles.append(m.role)
  for g in groups_of(user):
      for grant in grants_of(g):
          if grant.all_tenants or grant.tenant_id == tenant_id:
              roles.append(grant.role)
  return highest(roles)  # tenant_admin > operator > read_only ; None if empty
```

- **`app/core/deps.py::tenant_context`** — currently 403s unless `is_superadmin` or a `Membership` exists.
  Change: if not superadmin, compute `effective_role`; 403 only when it is `None`. Still calls
  `set_tenant_context(session, tenant_id)` for RLS exactly as today (a group grant changes *whether you may
  enter* a tenant, never the RLS scoping once in it).
- **`app/core/rbac.py::can`** — **unchanged**. It already takes `(is_superadmin, role, action)`; the resolver
  feeds it the effective role. `_ORG_ACTIONS` stay superadmin-only, so groups can never reach them.
- **`app/api/me_tenants.py`** — for a non-superadmin, return the union of: tenants with a direct membership +
  tenants reachable by a group grant. An **all-tenants** grant ⇒ list every tenant (like superadmin does),
  each with its effective role. (Superadmin path unchanged.) This drives the frontend tenant switcher + the
  `usePermissions` hook from #119, so an all-tenants `tenant_admin` group member gets the right UI surface
  across every client while still being denied the superadmin-only admin pages.

`highest()` precedence: `tenant_admin(3) > operator(2) > read_only(1)`.

## Org-level admin surface (superadmin only)

New `Action.GROUP_MANAGE` added to `_ORG_ACTIONS`. New `app/api/groups.py` (mirrors `app/api/users.py`,
`require_org(GROUP_MANAGE)`):
- `GET /api/groups` — list groups (+ member count, grant summary).
- `POST /api/groups` / `PATCH /api/groups/{id}` / `DELETE /api/groups/{id}` — CRUD.
- `PUT /api/groups/{id}/members` — set membership (add/remove users).
- `POST /api/groups/{id}/grants` / `DELETE /api/groups/{id}/grants/{grant_id}` — manage grants
  (all-tenants or a specific tenant + role).
- Every mutation writes an **audit** row (actor, group, change) like the existing admin endpoints.

Repositories: `GroupRepository`, `GroupGrantRepository` (owner/non-RLS reads for resolution; superadmin
session for management).

## Frontend

- New **superadmin-only** "Groups" admin page (nav alongside Users/Templates/SMTP/System; gated on
  `me.is_superadmin`): list groups, edit members (user multiselect), edit grants (scope = "All tenants"
  toggle OR a tenant picker, + role select). New `i18n` keys (en + 11 locales, key parity enforced).
- The tenant switcher + `usePermissions` already consume `/api/me/tenants`; once the backend returns
  group-derived tenants+roles, no further frontend gating change is needed (the #119 hook handles roles).

## Testing

- **Resolution unit tests:** highest-of(direct, group, all-tenants) wins; no membership + covering grant ⇒
  access; no membership + no covering grant ⇒ 403; all-tenants grant ⇒ every tenant; specific-tenant grant ⇒
  only that tenant; read_only grant cannot write.
- **`tenant_context` / API tests:** a group-only user can list/act on a granted tenant at the granted role,
  and is 403 on a non-granted tenant and on every `_ORG_ACTIONS` endpoint.
- **RLS isolation tests (critical):** a group member with an all-tenants grant, while acting in tenant A,
  sees ONLY tenant A's rows (group reach must not widen RLS scope within a tenant). Cross-tenant data never
  leaks. (Extends `test_rls_isolation.py`.)
- **`/api/me/tenants`:** union of direct + group-derived tenants with correct effective roles.
- **Admin API:** group/member/grant CRUD is superadmin-only (403 for tenant_admin/operator), round-trips,
  audited.
- **Frontend:** Groups page renders for superadmin only; grant editor toggles all-tenants vs tenant pick.

## Security review checklist (must pass before merge)

- Group reach widens *tenant entry*, never *RLS scope* once inside a tenant.
- Groups can never grant an `_ORG_ACTIONS` capability (no privilege escalation to superadmin powers).
- `group_grants.role` is constrained to the three tenant roles (no `superadmin`/org role injectable).
- Resolution reads of group tables don't leak group/tenant existence across tenants in API responses.
- A `read_only` all-tenants grant stays read-only everywhere (highest() can't be tricked by an empty role).

## Out of scope (v1)

- Delegated/admin groups that carry org/critical permissions (explicitly rejected — superadmin-only stays).
- Nested groups, time-bound/expiring grants, per-device group grants.
- Self-service group requests; SCIM/IdP group sync (future, if ever).
