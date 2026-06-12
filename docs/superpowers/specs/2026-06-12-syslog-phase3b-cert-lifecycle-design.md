# Syslog Phase 3.2 — Certificate Lifecycle (rotation + soft revocation) design spec

**Date:** 2026-06-12
**Status:** Approved (design); writing implementation plan next.
**Milestone:** syslog log-pipeline, **Phase 3**, sub-project **3.2 of 4**
(3.1 provisioning UX — merged PR #67; **3.2 cert lifecycle — this spec**; 3.3 scale; 3.4 MSP dashboards).

## Goal

Give an operator two certificate-lifecycle actions for a device's mTLS log forwarding, from the same
device "Log forwarding" card:

- **Rotate** — replace the device's client certificate before it expires (or proactively), with no log
  loss, reusing only Phase-1-verified OPNsense primitives.
- **Revoke (soft)** — stop forwarding for a device whose key may be compromised: remove the cert/target
  from the box, and **record the revoked serial in a ledger** so the future CRL enforcement (3.2-bis)
  has the data it needs. The device shows a distinct "Revoked" state and only comes back via an
  explicit re-enable that issues a fresh certificate.

## Locked decisions (from brainstorming)

- **Revocation model = soft now, CRL-enforced later.** Revocation enforcement (rejecting a compromised
  key at the receiver) lives entirely in our syslog-ng (`crl-dir()`), independent of OPNsense, and its
  correct enforcement is **unverified** on our build (the Phase-1 config carries a
  "RUNTIME VERIFICATION REQUIRED" caveat; no staging bring-up yet). So 3.2 ships **soft** revocation +
  a **CRL-ready revocation ledger**; **3.2-bis** wires the CRL and verifies it during a staging bring-up.
- **Rotation uses only Phase-1-verified box primitives** (`import_cert`, `add_syslog_destination`,
  `delete_syslog_destination`, `delete_cert`) — **add the new destination first, then delete the old**
  (no log gap; transient duplicate docs for a few seconds are acceptable for a log store). We do **not**
  use the unverified `syslog/settings/setDestination`.
- **Trigger = manual** (button). Auto-renew before expiry is a noted future enhancement (the expiry +
  "expires soon" hint already ship in 3.1). **CA rotation is out of scope** (dedicated effort).

## What already exists (do not rebuild)

- `app/services/log_forwarding.py`: `provision_device(...)` (issues cert, imports CA+cert, adds the
  destination, stores `cert_serial/fingerprint/not_after/opnsense_*_uuid/provisioned_at`),
  `deprovision_device(...)` (deletes destination + cert, `enabled=False`), `SyslogCaService`.
- `app/connectors/opnsense/client.py`: `import_ca`, `import_cert`, `add_syslog_destination`,
  `delete_syslog_destination`, `delete_cert` (all reconfigure the box; verified Phase 1).
- `app/api/log_forwarding.py`: status (`DEVICE_VIEW`), `enable`/`disable` (`CONFIG_PUSH` + CSRF +
  audit, `OpnsenseError → 502`), `_out`, `_device`, `_client`.
- `app/models/device_log_forwarding.py` (+ `cert_not_after` from 3.1); `LogForwardingOut`
  (`…, cert_not_after, last_log_at`); `DeviceLogForwardingRepository`.
- RLS: `TENANT_TABLES` in `app/core/rls.py`; `policy_create_statement` / `grant_app_role_statements`.
- `cert_serial_and_fingerprint`, `cert_not_after` in `app/services/syslog_ca.py`.

So 3.2 is additive: two service functions, one ledger table + one column, two endpoints, two response
fields-of-state, two buttons.

## Components

### 1. Data model — migration 0026 + model

- **New table `revoked_syslog_certs`** (tenant-scoped, RLS — mirrors `device_log_forwarding`):
  `id` (uuid PK), `tenant_id` (uuid, FK tenants, RLS column), `device_id` (uuid, FK devices),
  `serial` (str — the revoked cert's serial, the CRL key), `reason` (str, nullable),
  `revoked_at` (timestamptz, default now). Register `"revoked_syslog_certs"` in `TENANT_TABLES`;
  the migration ENABLE+FORCE RLS, `policy_create_statement`, and grants the app role (mirror 0024).
- **New column `revoked_at TIMESTAMPTZ NULL`** on `device_log_forwarding` (the current-state marker for
  the "Revoked" badge). `provision_device` sets `row.revoked_at = None` on (re-)enable.

### 2. Service — `app/services/log_forwarding.py`

- `rotate_device_cert(session, *, tenant_id, device_id, client) -> DeviceLogForwarding`:
  require an existing enabled row (else raise a `ValueError` the API maps to 409). Issue a fresh device
  cert via `SyslogCaService.device_cert`; `import_cert` it; `add_syslog_destination` (new dest UUID);
  then `delete_syslog_destination(old_dest)` and `delete_cert(old_cert)`; update the row
  (`cert_serial/fingerprint/not_after`, `opnsense_cert_uuid=new`, `opnsense_dest_uuid=new`,
  `provisioned_at=now`). The CA UUID is unchanged. Add-first/delete-after → no log gap.
- `revoke_device(session, *, tenant_id, device_id, client, reason: str | None) -> DeviceLogForwarding`:
  load the enabled row (else 409). Insert the **current** `cert_serial` into `revoked_syslog_certs`
  (with tenant_id/device_id/reason). Deprovision the box (`delete_syslog_destination`, `delete_cert`).
  Set `row.enabled=False`, `row.revoked_at=now`. **All in one unit of work, committed only after the box
  calls succeed** (same all-or-nothing pattern as `enable`/`disable`): a box failure rolls back the
  ledger insert too, so recorded state never diverges from the box. *(In the soft model the box
  deletion is the only enforcement, so rolling back on box failure is correct. 3.2-bis may revisit this
  to commit the ledger first — CRL-first enforcement — once the CRL actually rejects the serial.)*

### 3. API — `app/api/log_forwarding.py`

- `POST .../log-forwarding/rotate` (`CONFIG_PUSH`, CSRF): call `rotate_device_cert`, audit
  `log_forwarding.rotate` (`details={"serial": new_serial}`), `OpnsenseError → 502`, `ValueError → 409`
  ("device is not currently forwarding"). Returns `LogForwardingOut`.
- `POST .../log-forwarding/revoke` (`CONFIG_PUSH`, CSRF): body `{reason?: str}`; call `revoke_device`,
  audit `log_forwarding.revoke` (`details={"serial": revoked_serial, "reason": reason}`),
  `OpnsenseError → 502`, `ValueError → 409`. Returns `LogForwardingOut`.
- `_out`: also map `revoked_at` from the row.

### 4. Schema — `app/schemas/log_forwarding.py`

- `LogForwardingOut` gains `revoked_at: datetime | None = None`.
- New `RevokeIn{reason: str | None = Field(default=None, max_length=500)}`.

### 5. Frontend — `LogForwardingCard` + hooks

- `logForwardingHooks.ts`: add `useRotateLogForwarding(deviceId)` and `useRevokeLogForwarding(deviceId)`
  (POST rotate / revoke; invalidate status on success). The revoke mutation takes `{reason?: string}`.
- `LogForwardingCard.tsx`:
  - When **enabled**: a **"Rotate cert"** button and a **"Revoke"** button (red), each behind a
    `ConfirmModal` (rotate: "Issues a new client certificate and swaps it on the device — no logs are
    lost."; revoke: "Removes the certificate and marks it revoked. Re-enabling will issue a brand-new
    certificate."). Both `CONFIG_PUSH`-gated (tenant_admin/operator).
  - A distinct **"Revoked"** badge state when `revoked_at` is set and `enabled` is false (vs plain
    "Disabled"); when revoked, the Enable button label hints "Re-enable (new cert)".
  - Generic error alert on rotate/revoke failure (same pattern as 3.1; no box detail leaked).

## Data flow

Operator opens the card → status (`enabled, cert_not_after, last_log_at, revoked_at`). **Rotate** →
confirm → `POST /rotate` → backend issues+swaps cert on the box → refetch (new fingerprint/expiry).
**Revoke** → confirm (optional reason) → `POST /revoke` → backend records the serial + deprovisions →
refetch (badge "Revoked"). **Re-enable** from revoked → existing `POST /enable` (fresh cert,
`revoked_at` cleared; the old serial stays in the ledger for 3.2-bis).

## Error handling

| Condition | Behaviour |
|-----------|-----------|
| Rotate/revoke on a non-enabled device | 409 (`ValueError`) — "device is not currently forwarding" |
| Box unreachable during rotate | 502; **add-first/delete-after** means the OLD destination is still active if it fails before the deletes → no log gap, no orphaned state committed (transaction not committed on raise) |
| Box unreachable during revoke (after serial snapshot, box delete fails) | 502; the transaction is **not committed** (the snapshot insert + box calls share one unit of work) → no partial DB state; operator retries. The ledger only persists on full success |
| read_only caller | 403 (RBAC); buttons hidden client-side |
| Device not in tenant | 404 (`_device` guard) |
| Re-enable a revoked device | normal `enable` → fresh cert, `revoked_at` cleared |

(Transactionality: rotate/revoke run inside the request's session and `commit()` only after the box
calls succeed, exactly like the existing `enable`/`disable` handlers — a box failure rolls back the DB
change, so the recorded state never diverges from the box.)

## Security

- Rotate/revoke are `CONFIG_PUSH` + CSRF + audited, like enable/disable. Status stays `DEVICE_VIEW`.
- `revoked_syslog_certs` is RLS tenant-scoped — a tenant only sees/affects its own revocations.
- No secrets surfaced: the ledger stores the **serial** (public), never the key; the card shows
  fingerprint/expiry/state only.
- Soft revocation is honestly labeled: it removes the cert from the box and records the serial, but
  **does not yet reject a compromised key at the receiver** — that is 3.2-bis (CRL). The ledger built
  here is exactly the CRL input.

## Testing

- **Service (stub client):** `rotate_device_cert` issues a new cert, adds a new dest, deletes the old
  dest+cert, and updates the row's serial/uuids (assert the new serial != old; old uuids deleted).
  `revoke_device` snapshots the serial into the ledger, deprovisions, sets `enabled=False` +
  `revoked_at`. Both raise on a non-enabled device. A box error rolls back (no ledger row, row
  unchanged).
- **API:** rotate/revoke require `CONFIG_PUSH` (operator OK, read_only 403); rotate on a disabled
  device → 409; audit rows written; `OpnsenseError → 502`; `_out` returns `revoked_at`.
- **Frontend (vitest + MSW):** the card shows Rotate + Revoke when enabled; rotate confirm → POST +
  refetch; revoke confirm → POST + "Revoked" badge; read_only hides both. `npm run build` green.

## Out of scope

- **3.2-bis:** CRL generation from `revoked_syslog_certs` + syslog-ng `crl-dir()` wiring + a staging
  bring-up that verifies the receiver actually rejects a revoked serial.
- **Auto-renew** before expiry (a worker job).
- **CA rotation** (re-key the CA + re-issue every device cert + re-import on every box).
