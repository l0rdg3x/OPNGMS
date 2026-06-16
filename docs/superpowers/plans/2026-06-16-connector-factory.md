# Connector Factory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** DRY the 17 duplicated `OpnsenseClient(device…)` construction sites into one `client_for_device(device)` factory that also wires the device's detected edition/version.

**Architecture:** A new `app/services/device_client.py` exposes `client_for_device(device)`; the 17 worker + API-router sites call it instead of re-constructing the client inline. Behavior-preserving for the managed fleet (edition/version wiring is a no-op for 26.1.x). `onboarding.probe_device` (raw-args detection) is untouched.

**Tech Stack:** Python 3.14 / SQLAlchemy model `Device` / `app.core.crypto` (Fernet) / pytest.

Spec: `docs/superpowers/specs/2026-06-16-connector-factory-design.md`.

---

## File Structure

- **Create** `backend/app/services/device_client.py` — the `client_for_device` factory (one responsibility: device row → connector client).
- **Create** `backend/tests/test_device_client.py` — unit + equivalence tests.
- **Modify** `backend/app/worker.py` (7 sites), `backend/app/api/config.py` (3), `backend/app/api/{monit,settings,firewall_rules,firmware,ids,catalog,log_forwarding}.py` (1 each) — call the factory; drop now-unused `crypto` / `OpnsenseClient` imports.

---

### Task 1: The `client_for_device` factory (TDD)

**Files:**
- Create: `backend/app/services/device_client.py`
- Test: `backend/tests/test_device_client.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_device_client.py
from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.models.device import Device
from app.services.device_client import client_for_device


def _device(**over):
    d = Device()
    d.base_url = "https://10.0.0.1"
    d.api_key_enc = crypto.encrypt("the-key")
    d.api_secret_enc = crypto.encrypt("the-secret")
    d.verify_tls = True
    d.tls_fingerprint = "AA:BB"
    d.edition = "community"
    d.firmware_series = "26.1"
    for k, v in over.items():
        setattr(d, k, v)
    return d


def test_builds_client_with_decrypted_creds_and_tls():
    c = client_for_device(_device())
    assert isinstance(c, OpnsenseClient)
    assert c._base_url == "https://10.0.0.1"
    assert c._auth == ("the-key", "the-secret")      # Fernet round-trips through decrypt
    assert c._verify is True
    assert c._fingerprint == "AA:BB"


def test_passes_edition_and_version_to_the_resolver():
    c = client_for_device(_device())
    assert c._resolver.edition == "community"
    assert c._resolver.vtuple == (26, 1, 0, 0)       # firmware_series "26.1" parsed


def test_verify_tls_false_and_no_fingerprint_are_honoured():
    c = client_for_device(_device(verify_tls=False, tls_fingerprint=None))
    assert c._verify is False and c._fingerprint is None


def test_equivalent_to_inline_construction_for_a_26x_device():
    # The edition/version wiring must NOT change endpoint resolution for the managed (26.1.x) fleet:
    # the version-sensitive capability (dns_events, legacy boundary at 20.1) resolves to the SAME default
    # spec whether or not edition/version were supplied. This is the behavior-preserving guarantee.
    d = _device()
    factory = client_for_device(d)
    inline = OpnsenseClient(
        d.base_url, crypto.decrypt(d.api_key_enc), crypto.decrypt(d.api_secret_enc),
        verify_tls=d.verify_tls, tls_fingerprint=d.tls_fingerprint,
    )
    assert factory._resolver.resolve("dns_events") is inline._resolver.resolve("dns_events")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_device_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.device_client'`.

(Note: `crypto.encrypt/decrypt` need the test `MASTER_KEY`, already configured by the test harness — no DB needed; `Device()` is built in memory.)

- [ ] **Step 3: Implement the factory**

```python
# backend/app/services/device_client.py
from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.models.device import Device


def client_for_device(device: Device) -> OpnsenseClient:
    """Build an SSRF-guarded OpnsenseClient from a persisted device row.

    Decrypts the Fernet-encrypted API creds and applies the device's TLS settings plus its detected
    edition/version, so the version-aware capability matrix resolves to the right endpoints. The
    per-request timeout comes from OPNSENSE_HTTP_TIMEOUT inside the client. Secrets are decrypted only
    in memory here — never logged or returned.
    """
    return OpnsenseClient(
        device.base_url,
        crypto.decrypt(device.api_key_enc),
        crypto.decrypt(device.api_secret_enc),
        verify_tls=device.verify_tls,
        tls_fingerprint=device.tls_fingerprint,
        edition=device.edition,
        version=device.firmware_series,
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_device_client.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/device_client.py backend/tests/test_device_client.py
git commit -m "refactor(connector): add client_for_device factory (decrypt + tls + edition/version)"
```

### Task 2: Migrate the 17 device-row construction sites

**Files (modify):** `backend/app/worker.py`, `backend/app/api/config.py`, `backend/app/api/monit.py`, `backend/app/api/settings.py`, `backend/app/api/firewall_rules.py`, `backend/app/api/firmware.py`, `backend/app/api/ids.py`, `backend/app/api/catalog.py`, `backend/app/api/log_forwarding.py`.

- [ ] **Step 1: Replace each construction site with the factory**

In every module above, add `from app.services.device_client import client_for_device` and replace each
inline construction:

```python
client = OpnsenseClient(
    device.base_url,
    crypto.decrypt(device.api_key_enc),
    crypto.decrypt(device.api_secret_enc),
    verify_tls=device.verify_tls,
    tls_fingerprint=device.tls_fingerprint,
)
```

with:

```python
client = client_for_device(device)
```

The two single-line helper forms (`worker.py:327`, `log_forwarding.py:34` — `return OpnsenseClient(device.base_url, crypto.decrypt(...), …)`) become `return client_for_device(device)`. Sites are listed by `cd backend && grep -rn "OpnsenseClient(" app/ | grep -v onboarding.py` — confirm the count is 17 across the 9 files before and 0 after (onboarding's `probe_device` is the only remaining `OpnsenseClient(` in `app/`).

- [ ] **Step 2: Remove now-unused imports**

In each migrated module, drop the `OpnsenseClient` import and the `crypto` import **iff** they are no
longer referenced in that module (some modules use `crypto` elsewhere — e.g. for `crypto.encrypt` on
write paths — keep those). Let ruff tell you:

Run: `cd backend && ruff check app/`
Expected: it flags any now-unused import (`F401`); remove exactly those, re-run until clean.

- [ ] **Step 3: Verify behavior is preserved**

Run: `cd backend && python -m pytest tests/test_worker_config.py tests/test_poller_e2e.py tests/test_monitoring.py tests/test_connector_config.py tests/test_config_push_api.py tests/test_log_forwarding_api.py tests/test_firmware_action_service.py -q`
Expected: PASS (the worker + API-router paths that build a client still behave identically).

- [ ] **Step 4: Commit**

```bash
git add backend/app/worker.py backend/app/api/
git commit -m "refactor(connector): route the 17 device-row client sites through client_for_device"
```

### Task 3: Backend gate + open PR

- [ ] **Step 1: Full suite + lint**

Run: `cd backend && ruff check app/ && python -m pytest -q`
Expected: ruff clean; all tests pass (behavior-preserving — no test should need changing; if a test
constructed the client inline and asserted on it, update it to the factory only if it is testing the
construction itself).

- [ ] **Step 2: Push + PR**

```bash
git push -u origin refactor/device-client-factory
```
Open a PR to `main`: `refactor(connector): client_for_device factory (perf+refactor 1/4)`. Body = the spec
link + the DRY summary (17 sites → 1 factory, now version-aware). Green CI → squash-merge.

---

## Self-review (plan vs spec)

- **Spec coverage:** factory module (T1) ✓; decrypt + verify_tls + tls_fingerprint + edition/version (T1 impl + tests) ✓; 17-site migration + dead-import removal (T2) ✓; probe_device untouched (T2 note) ✓; equivalence-for-fleet test (T1 `test_equivalent_to_inline_construction_for_a_26x_device`) ✓; full-suite regression (T3) ✓; invariants — secrets in-memory only, client/SSRF/timeout untouched (factory just forwards) ✓.
- **Placeholder scan:** none — every code step is complete; the import-removal step is ruff-driven (concrete command + expected F401), not a vague "clean up".
- **Type/name consistency:** `client_for_device(device: Device) -> OpnsenseClient` used identically in the factory, all tests, and every migration site; resolver attrs `_resolver.edition` / `_resolver.vtuple` match `CapabilityResolver`; client attrs `_base_url`/`_auth`/`_verify`/`_fingerprint` match the constructor.
