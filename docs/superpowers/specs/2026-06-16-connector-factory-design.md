# Connector Factory (`client_for_device`) — Design Spec

**Date:** 2026-06-16
**Status:** Approved (design); writing the implementation plan next.
**Milestone:** Performance + refactor — **sub-project 1 of 4** (refactor; then backend perf, frontend
bundle, large-file splits). Behavior-preserving, test-guarded.

## Goal

DRY the **17 duplicated `OpnsenseClient(device…)` construction sites** (worker ×7, API routers ×10) into a
single **`client_for_device(device)`** factory. Today each site repeats the same 5-line construction
(decrypt both creds, pass `verify_tls` + `tls_fingerprint`) — and **none of them pass the device's detected
`edition`/`version`**, so the version-aware capability matrix silently resolves with the default profile at
every call. The factory removes the duplication and centralizes the construction (creds decryption, TLS
settings, edition/version, and — already inside the client — the configured timeout).

## Current state (measured)

- 17 sites build `OpnsenseClient(device.base_url, crypto.decrypt(device.api_key_enc),
  crypto.decrypt(device.api_secret_enc), verify_tls=device.verify_tls,
  tls_fingerprint=device.tls_fingerprint)` — in `app/worker.py` (7), `app/api/config.py` (3), and one each
  in `app/api/{monit,settings,firewall_rules,firmware,ids,catalog,log_forwarding}.py`. A few are already
  tiny per-module helpers (`worker.py:327`, `log_forwarding.py:34` return a constructed client).
- The `Device` model persists `edition` (default "") and `firmware_version` (nullable, e.g. "26.1.10"),
  detected + stored during onboarding/monitoring (the multi-version resolver). **No generic construction
  site passes them** (only the catalog router already passed `firmware_version`), so `OpnsenseClient`'s
  `CapabilityResolver("","")` runs with defaults at the other 16 sites.
- `app/services/onboarding.py::probe_device(base_url, api_key, api_secret, …)` builds a client from **raw**
  args (pre-persistence connect-test, edition still being detected) — a legitimately different entry point.

## Design

### Component: `app/services/device_client.py`

A new small module (depends on `Device` + `crypto` + `OpnsenseClient`, keeping the connector layer itself
decoupled from the model/crypto):

```python
from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.models.device import Device


def client_for_device(device: Device) -> OpnsenseClient:
    """Build an SSRF-guarded OpnsenseClient from a persisted device row: decrypt the API creds and apply
    the TLS settings + the detected edition/version so the version-aware capability matrix resolves to the
    right endpoints. The per-request timeout comes from OPNSENSE_HTTP_TIMEOUT inside the client."""
    return OpnsenseClient(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
        edition=device.edition,
        version=device.firmware_version or "",
    )
```

### Migration

Replace all 17 device-row construction sites (and any tiny per-module client helper they currently use)
with `client_for_device(device)`. Remove the now-dead `crypto`/`OpnsenseClient` imports that become unused
in those modules. **`probe_device` stays as-is** (raw-args detection path, no `Device`, no decryption).

### The edition/version decision (approved)

The factory **passes `edition`/`version`** (today's sites do not). For the real fleet (Community 26.1.x)
the resolver returns the **same** endpoints as the current default — so this is **behavior-identical in
practice**; for older/Business devices it becomes *more* correct (closing a latent resolver gap). Verified
by an equivalence test (below). This is the only intended behavioral delta and it is a no-op for the
managed fleet. The factory passes the **full `firmware_version`** (not the YY.M series): this preserves the
exact prior behavior of the one site that already passed a version (the catalog router) and is strictly
more precise; `None` (not-yet-probed) -> `""` -> the resolver's newest profile.

## Invariants (unchanged)

- **Secrets at rest:** creds are Fernet-decrypted only in memory at construction (exactly as today); never
  logged, never returned. The factory adds no logging of secret material.
- **SSRF guard / TLS / timeout:** `OpnsenseClient` is untouched — same HTTPS-only, no-redirect,
  loopback/link-local-blocking client, same `verify_tls`/pinning, same `OPNSENSE_HTTP_TIMEOUT`.
- **RLS / tenant scoping:** the factory takes an already-loaded `Device` (resolved under the caller's
  tenant context); it touches no query path.

## Testing

- **Unit (`tests/test_device_client.py`):** `client_for_device` on a `Device` with encrypted creds returns
  an `OpnsenseClient` whose `base_url`, decrypted auth tuple, `verify_tls`, `tls_fingerprint` match, and
  whose resolver carries the device's `edition`/`firmware_version`. Uses the real `crypto` (encrypt a fixture
  secret, assert it round-trips through decrypt).
- **Equivalence:** for a 26.1.x community device, the factory-built client resolves the same endpoint as a
  client built the old inline way for a representative capability (e.g. `dns_events`) — proves the
  edition/version wiring is behavior-preserving for the fleet.
- **Regression:** the full backend suite stays green (worker, every API router that builds a client, the
  connector tests) — the refactor changes construction, not behavior. `ruff check app/` clean (no unused
  imports left behind).

## Out of scope (later sub-projects)

Backend perf (singleton ARQ pool + N+1 triage + indexes), frontend bundle, large-file splits — each its own
spec/plan. This PR is the pure connector-construction DRY.
