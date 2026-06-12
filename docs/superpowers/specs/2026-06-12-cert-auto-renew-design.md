# Device cert auto-renewal (C14) — design spec

**Date:** 2026-06-12
**Status:** Approved (batch directive); writing plan next.
**Context:** A worker cron that proactively **rotates per-device log-forwarding certs before they
expire**, riding the merged 3.2 `rotate_device_cert`. Also the pragmatic mitigation for the 3.2-bis
CRL blocker (syslog-ng `crl-dir()` doesn't enforce revocation on the 4.5.0 build) — short-lived certs +
auto-renew bound the window in which a compromised-but-not-yet-expired cert is usable.

## Goal

Devices whose forwarding cert nears expiry get a fresh cert + box-side swap **automatically**, so
forwarding never silently breaks on expiry and the cert lifetime can be kept short.

## What exists (reuse)

- `app/services/log_forwarding.py` `rotate_device_cert(session, *, tenant_id, device_id, client,
  receiver_host, receiver_port)` — issues a fresh cert, add-new-then-delete-old swap on the box, updates
  the row (`cert_serial/fingerprint/not_after/opnsense_*_uuid/provisioned_at`). Requires `enabled`.
- `device_log_forwarding{enabled, cert_not_after, tenant_id, device_id, …}` (3.1/3.2).
- `app/api/log_forwarding.py::_client(device)` pattern — builds `OpnsenseClient(device.base_url,
  decrypt(api_key_enc), decrypt(api_secret_enc), verify_tls=…, tls_fingerprint=…)`.
- Worker (`app/worker.py`): owner session `ctx["session_factory"]` (RLS-exempt), `cron(...)` jobs,
  `AuditService`. Mirror `sweep_orphaned_actions` (owner, per-row transaction, one bad row can't abort).

## Components

### 1. Settings — `app/core/config.py`
- `cert_renewal_window_days: int = 30` — renew when `cert_not_after < now + window`.
- `cert_renewal_hour: int = 3` — daily UTC hour the cron runs.

### 2. Service — `app/services/cert_renewal.py`
- `def due_for_renewal(cert_not_after: datetime | None, *, now: datetime, window: timedelta) -> bool`
  — pure: `True` iff `cert_not_after is not None and cert_not_after < now + window`. (A null expiry is
  not auto-renewed — it predates 3.1; left for manual rotation.)
- `async def renew_expiring_device_certs(session, settings, *, client_for) -> dict` — query enabled
  `device_log_forwarding` rows; for each whose `cert_not_after` is `due_for_renewal`, call
  `rotate_device_cert(session, tenant_id=row.tenant_id, device_id=row.device_id,
  client=client_for(device), receiver_host=settings.syslog_receiver_host,
  receiver_port=settings.syslog_tls_port)`. `client_for(device)` is an injected callable (real one in
  the worker; a stub in tests). Returns `{"renewed": int, "failed": int, "considered": int}`. Each
  device in its own try/except — a box-unreachable device increments `failed` and is retried next run
  (does NOT abort the batch). Runs under the owner session (sees all tenants; no per-tenant RLS context
  needed — same as the worker's other cross-tenant jobs).

### 3. Worker cron — `app/worker.py`
- `async def renew_device_certs(ctx) -> dict` — builds `client_for` from each device's stored creds
  (the `_client` pattern, importing `OpnsenseClient` + `crypto`), opens an owner session from
  `ctx["session_factory"]`, calls `renew_expiring_device_certs`, audits `log_forwarding.auto_renew`
  per renewed device (actor_user_id=None — system action), returns the summary.
- Register: `cron(renew_device_certs, hour={_settings.cert_renewal_hour}, minute={0})` in
  `WorkerSettings.cron_jobs`.

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| `cert_not_after` null | skipped (not due) |
| not yet within the window | skipped |
| device disabled | not selected (query filters `enabled=True`) |
| box unreachable / `OpnsenseError` during rotate | counted `failed`, logged; retried next daily run; does NOT abort the batch |
| rotate raises `ValueError` (not forwarding) | counted `failed`, continue |

## Security
- Renewal is a system action (no user); it only re-issues a cert for an already-enabled device (no new
  trust). Audited. Runs owner-side like the other worker jobs. No secret logged (fingerprint only).

## Testing
- `due_for_renewal`: null→False; far-future→False; within window→True; past→True.
- `renew_expiring_device_certs` (DB + stub `client_for`): a seeded enabled row with a near-expiry
  `cert_not_after` is renewed (rotate called, counts `renewed=1`); a far-expiry row is skipped; a
  `client_for` that raises on one device → that device `failed`, others still processed.
- Worker wiring is thin; covered by the service tests + a registration smoke (the cron is in
  `WorkerSettings.cron_jobs`).

## Out of scope
- CRL hard-revocation (3.2-bis, blocked — separate decision). Configurable per-device renewal cadence.
  Alerting on repeated renewal failure (a follow-up; today it just retries daily + logs).
