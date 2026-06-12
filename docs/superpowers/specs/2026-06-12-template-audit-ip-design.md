# C7 — minor template items: per-operation IP in audit + escape-hatch decision

Two small, related items from the batch backlog.

## Part 1 (built): per-operation client IP in the template/profile audit

**Problem:** every `AuditService.record(...)` call in `api/templates.py` and `api/profiles.py`
passed `ip=None`, and the apply handlers didn't even receive `request: Request`. So template/profile
operations — including the security-relevant **apply** (which schedules a real config push to a
customer device) — were audited with no source IP, unlike `config.py`'s change endpoints, which
record `ip=request.client.host if request.client else None`.

**Fix:** add `request: Request` to every mutating template/profile handler and record the client IP,
matching the `config.py` idiom exactly. Covered operations:

- templates: `template.create`, `template.update`, `template.delete`, `template.override`,
  `template.apply`
- profiles: `profile.create`, `profile.update`, `profile.delete`, `profile.apply`

The `AuditLog.ip` column and `AuditService.record(ip=...)` already exist — this only wires the value
through. For a profile apply that fans out to N changes, the single `profile.apply` row carries the
IP (the operator's request); each fanned-out change is applied later by the worker (no per-change
HTTP client to attribute).

**Tests:** the existing `test_apply_template_writes_audit_row` and
`test_apply_profile_fans_out_two_jobs` now also assert `row.ip == "127.0.0.1"` (the ASGI test
client's address).

## Part 2 (not built — already exists): raw/advanced escape-hatch

The backlog item was "raw/advanced escape-hatch for a kind **(as needed)**". It is **not needed**: a
raw passthrough already exists at the change layer. `POST
…/devices/{id}/config/changes` (`ConfigChangeIn`) accepts an **arbitrary** `kind` /
`operation` / `target` / `payload` with no curated template schema, gated by `CONFIG_PUSH` + CSRF;
`apply_for_kind` simply requires a registered applier at apply time (else `UnknownChangeKindError`).

A "raw template kind" would duplicate that with worse ergonomics (templates are a curated,
override-able library; a schemaless template undermines that contract). So we deliberately do **not**
add one (YAGNI). If a future need arises for a *reusable, library-stored* raw change, revisit then.
