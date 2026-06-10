# OPNGMS — SEC-2: TLS Certificate Fingerprint Pinning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Make the OPNsense connector **pin the device cert's SHA-256 fingerprint** (opt-in, when `Device.tls_fingerprint` is set) so a `verify_tls=False` self-signed connection becomes MITM-resistant — verified BEFORE any credentials are sent — while keeping `verify_tls=False` **without** a fingerprint permissive (accepts self-signed, unchanged) per the user's requirement. The SSRF guard stays **byte-identical**.

**Spec:** `docs/superpowers/specs/2026-06-10-opngms-sec2-tls-pinning-design.md`.

**Tech Stack:** Python `ssl`/`asyncio`, hashlib, httpx; `cryptography` (test self-signed cert); pytest.

---

## Context for the implementer (read first)

- `app/connectors/opnsense/client.py` `OpnsenseClient.__init__(base_url, api_key, api_secret, *, verify_tls=True, timeout=10.0)` stores `self._verify`. `_request(path, method, json)` does the **SSRF guard**: `pinned_ip, host, port = validate_base_url(self._base_url)` (DO NOT change this or the IP-pin/Host/SNI/`follow_redirects=False` logic — it must stay byte-identical), builds the URL against the pinned IP, then `httpx.AsyncClient(verify=self._verify, ..., auth=self._auth, follow_redirects=False)` + `client.request(..., headers={"Host": host}, extensions={"sni_hostname": host}, json=json)`. `ReachabilityError`/`AuthError`/`ApiError` are the connector errors.
- `Device.tls_fingerprint: Mapped[str | None]` exists (`app/models/device.py`).
- Construction sites: `app/services/onboarding.py:23` (`probe_device` already has a `tls_fingerprint` param but doesn't pass it), `app/api/config.py:135`, `app/worker.py:43,73,102,126`. Each has a `device` object (or raw params for onboarding).
- `cryptography` is a backend dependency (use it to build a self-signed cert in tests). `respx` mocks httpx (use it to assert NO request is made on a pin mismatch).

**Commands** (backend): `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q`.

---

## Task 1: Pinning helper

**Files:** Create `app/connectors/opnsense/tls_pinning.py`, `tests/test_tls_pinning.py`.

- [ ] **Step 1: Implement** `app/connectors/opnsense/tls_pinning.py`:
```python
"""TLS certificate fingerprint pinning for self-signed OPNsense devices.

When an operator pins a SHA-256 fingerprint, the connector verifies the device's leaf certificate
matches it BEFORE sending credentials (MITM-resistant). CERT_NONE is used ONLY to retrieve the peer
certificate for the fingerprint comparison — the comparison itself is the verification.
"""
import asyncio
import hashlib
import ssl


class PinMismatchError(Exception):
    """The peer certificate fingerprint did not match the pinned value."""


def normalize_fingerprint(value: str) -> str:
    v = value.strip().lower()
    if v.startswith("sha256:"):
        v = v[len("sha256:"):]
    return v.replace(":", "").replace(" ", "")


async def peer_fingerprint(host: str, ip: str, port: int, *, timeout: float) -> str:
    """Connect to the (SSRF-pinned) IP with SNI=host and return the leaf cert's SHA-256 hex."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # retrieve-only; the fingerprint match is the actual check
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host=ip, port=port, ssl=ctx, server_hostname=host),
        timeout=timeout,
    )
    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        der = ssl_obj.getpeercert(binary_form=True) if ssl_obj else None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001 — best-effort close
            pass
    if not der:
        raise ssl.SSLError("no peer certificate")
    return hashlib.sha256(der).hexdigest()


async def verify_pinned(host: str, ip: str, port: int, expected: str, *, timeout: float) -> None:
    """Raise PinMismatchError if the peer cert's SHA-256 != the pinned fingerprint."""
    actual = await peer_fingerprint(host, ip, port, timeout=timeout)
    if actual != normalize_fingerprint(expected):
        raise PinMismatchError()
```

- [ ] **Step 2: Tests** `tests/test_tls_pinning.py`:
  - `test_normalize_fingerprint`: `normalize_fingerprint("AA:BB:cc")` == `"aabbcc"`; strips a `sha256:` prefix and whitespace; lowercases.
  - A fixture that **builds a self-signed cert + key** with `cryptography` (CN=localhost, SAN localhost/127.0.0.1), writes them to temp files, computes the expected SHA-256 of the DER, and starts an `asyncio.start_server` TLS server on `127.0.0.1:0` (an ssl server context loaded with the cert+key; the handler can just sleep/close — only the handshake matters). Yield `(port, expected_hex)`; close the server after.
  - `test_peer_fingerprint_matches`: `await peer_fingerprint("localhost", "127.0.0.1", port, timeout=5)` == the expected hex.
  - `test_verify_pinned_ok`: `await verify_pinned("localhost", "127.0.0.1", port, expected_hex, timeout=5)` does not raise (also accept a colon-formatted/upper variant).
  - `test_verify_pinned_mismatch`: `await verify_pinned(..., "00"*32, ...)` raises `PinMismatchError`.
  Run + commit `feat(security): TLS fingerprint pinning helper (peer_fingerprint/verify_pinned)`.

---

## Task 2: Connector integration

**Files:** Modify `app/connectors/opnsense/client.py`; Test `tests/test_connector_tls_pinning.py`.

- [ ] **Step 1: `__init__`** — add `tls_fingerprint: str | None = None` to `OpnsenseClient.__init__` (after `verify_tls`); store `self._fingerprint = tls_fingerprint`.
- [ ] **Step 2: `_request` pre-flight** — in `_request`, AFTER `pinned_ip, host, port = validate_base_url(...)` (and its except), BEFORE building the httpx client, add:
```python
        # TLS pinning (opt-in): when not doing CA verification but a fingerprint is pinned, verify the
        # device cert BEFORE sending credentials. No fingerprint => permissive (self-signed) as before.
        if not self._verify and self._fingerprint:
            from app.connectors.opnsense.tls_pinning import PinMismatchError, verify_pinned
            try:
                await verify_pinned(host, pinned_ip, port or 443, self._fingerprint, timeout=self._timeout)
            except PinMismatchError as exc:
                raise ReachabilityError("certificate fingerprint mismatch") from exc
            except (ssl.SSLError, OSError, asyncio.TimeoutError) as exc:
                raise ReachabilityError("device unreachable") from exc
```
Add `import ssl` and `import asyncio` at the top of `client.py` if not present. **Do NOT touch** the SSRF guard, the URL building, `verify=self._verify`, `follow_redirects=False`, Host/SNI, or the error mapping.
- [ ] **Step 2b: Tests** `tests/test_connector_tls_pinning.py` (use `respx` to assert the request is/ISN'T made):
  - **Mismatch aborts before any request**: monkeypatch `app.connectors.opnsense.tls_pinning.verify_pinned` (or patch the symbol imported in client) to raise `PinMismatchError`; build `OpnsenseClient(base, k, s, verify_tls=False, tls_fingerprint="ab"*32)`; with a respx mock registered, call a method (e.g. `test_connection`) and assert it raises `ReachabilityError` AND respx recorded **0** requests (creds never sent). (Because the pre-flight import is inside `_request`, patch `app.connectors.opnsense.tls_pinning.verify_pinned`.)
  - **Match proceeds**: monkeypatch `verify_pinned` to return (no raise); with respx mocking the system-info endpoint to a valid response, assert the call succeeds and respx recorded the request, and that `verify_pinned` was called once with `(host, pinned_ip, port_or_443, fingerprint)`.
  - **No fingerprint → permissive (verify_pinned NOT called)**: `verify_tls=False, tls_fingerprint=None`; monkeypatch `verify_pinned` to a spy that fails the test if called; assert the request proceeds (respx) without calling it.
  - **verify_tls=True → verify_pinned NOT called**: same spy; the CA path is unchanged.
  Run + commit `feat(security): connector pins the device cert fingerprint before sending credentials`.

---

## Task 3: Wire `device.tls_fingerprint` into the construction sites

**Files:** Modify `app/services/onboarding.py`, `app/api/config.py`, `app/worker.py`; Tests as needed.

- [ ] **Step 1:** `app/services/onboarding.py` `probe_device` — pass its existing `tls_fingerprint` param to the client: `OpnsenseClient(base_url, api_key, api_secret, verify_tls=verify_tls, tls_fingerprint=tls_fingerprint)`. (Confirm the test-connection endpoint passes the device's `tls_fingerprint` into `probe_device`; if not, wire it there too — read `app/api/devices.py` test-connection handler.)
- [ ] **Step 2:** `app/api/config.py:135` and the 4 `app/worker.py` sites — each constructs `OpnsenseClient(device.base_url, crypto.decrypt(...), crypto.decrypt(...), verify_tls=device.verify_tls)`; add `tls_fingerprint=device.tls_fingerprint`.
- [ ] **Step 3:** Tests — confirm the existing connector/worker tests still pass (default `tls_fingerprint=None` → no behaviour change). Add/adjust an assertion in the test-connection path that the fingerprint is threaded through if easy. Run the FULL suite green.
- [ ] **Step 4:** Commit `feat(security): thread device.tls_fingerprint into all OpnsenseClient constructions`.

---

## Task 4: Technical debt
- Append SEC-2 debt: the pre-flight opens an extra TLS handshake per request (the connector builds a client per request — a known debt; verify-once/caching is a later optimisation). **Fingerprint provisioning UX** (display the observed fingerprint on test-connection, or a TOFU capture) is the immediate follow-up so operators can obtain the value to pin. Commit `docs: technical debt SEC-2`.

---

## Definition of "Done" (SEC-2)
- `verify_tls=False` + a pinned fingerprint → the connector verifies the device cert before sending creds
  and aborts (sanitized) on mismatch; `verify_tls=False` + no fingerprint → still accepts self-signed;
  `verify_tls=True` → unchanged CA verification. SSRF guard byte-identical. All construction sites pass the
  fingerprint. Backend suite green.
