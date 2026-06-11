# monit_test — optional auto-attach to the Monit system service

**Status:** Design (follow-up #4 of the 2026-06-11 TODO plan). Small extension to the existing `monit_test` kind.
**Date:** 2026-06-11

## Goal

A `monit_test` template creates a reusable Monit test, but a test does nothing until it is **attached
to a service**. Add an opt-in flag so applying the template can also **attach the test to the device's
`system` service** (where SystemResource tests like CPU/memory/load live). Default off (non-destructive,
preserves today's behavior).

## Verified OPNsense API (real box 26.1.9, read + revertible write)

- `GET monit/settings/searchService` → rows incl. `{uuid, name, type, tests}`. The **system** service
  has `type == "system"` (name `$HOST` on the box). Exactly one system service is expected.
- `GET monit/settings/getService/{uuid}` → `{"service": {…, "tests": {<uuid>:{value,selected}}}}` — the
  `tests` field is an option-set of test UUIDs; selected = the attached tests.
- **Attach = PARTIAL set, merges:** `POST monit/settings/setService/{uuid} {"service": {"tests": "<comma-joined selected uuids, incl. the new one>"}}` → `{"result":"saved"}`. Verified: created a probe test, attached it to the system service (4→5 tests), then reverted (restore tests + delTest) — clean, no residue.

## Design

- **Template body:** add an optional `attach_to_system` field (`"0"|"1"`, default `"0"`). It is a
  *directive*, not a Monit field — it is NOT part of the identity (`name`) and is **stripped from the
  test payload** before the test is sent to OPNsense.
- **Connector** (`apply_monit_test`): pop `attach_to_system` from the payload; upsert the test as today
  (obtaining its uuid from add/set); if the flag is truthy AND not dry-run, resolve the system service
  and attach:
  - `_resolve_system_service_uuid()` → `searchService`, pick the row with `type == "system"`; refuse
    (ApiError) if zero or >1 (never mutate on doubt).
  - read the service's current selected test uuids (`getService`), add the new uuid if absent, and
    `setService/{sid} {"service":{"tests": <comma-joined>}}` (partial merge). Idempotent: if already
    attached, no-op. Then the existing `monit/service/reconfigure`.
- **Kind validator** (`monit_kind._validate`): unchanged behavior; `attach_to_system` (if present) must
  be `"0"`/`"1"` (else reject). Identity/`pinned` unchanged (`name`).
- **Frontend** (`MonitTestForm`): a "**Attach to the system service**" checkbox (testid
  `monit-attach-system`) outside the introspection auto-form, writing `attach_to_system` `"0"|"1"` into
  the body. A short note explains it attaches the test to the device's system service so it takes effect.

## Testing
- Backend connector (respx): with `attach_to_system="1"` and an existing/absent system-service test, the
  flow does searchService → getService → setService(merged tests) → reconfigure; with `"0"`, no service
  calls; dry-run mutates nothing; ambiguous/zero system service → ApiError. Kind validator accepts the
  flag and strips it from the sent test payload (the addTest body has no `attach_to_system`).
- Frontend: the checkbox toggles `attach_to_system` in the body.
- **Live verify** (revertible): apply a `monit_test` with attach on; confirm the test exists AND is in
  the system service's tests; then detach + delete + reconfigure.

## Out of scope
- Attaching to a non-system service / arbitrary service selection (the curated case is the system
  service). Detaching/removing on disable. Both are future follow-ups.
