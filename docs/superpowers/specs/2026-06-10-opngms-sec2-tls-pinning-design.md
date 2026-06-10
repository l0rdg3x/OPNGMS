# OPNGMS — Security Milestone SEC-2: TLS Certificate Fingerprint Pinning — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user authorised SEC-2 and clarified: a self-signed cert with `verify_tls=false` must still be accepted when no fingerprint is pinned)
- **Milestone:** SEC-2 — the last P0 connector security item (TLS pinning; addresses the #1 flagged debt: MITM with `verify_tls=False`)
- **Depends on:** the OPNsense connector + the `Device.tls_fingerprint` field (stored but ignored today) in `main`
- **Enables:** MITM-resistant device connections even with self-signed certs (when a fingerprint is pinned)

## 1. Context & goal

OPNsense firewalls commonly use **self-signed** certs, so operators set `verify_tls=False` — which today
disables certificate verification entirely (MITM risk; the connector's SSRF guard pins the IP but not the
cert). `Device.tls_fingerprint` exists but is ignored. SEC-2 makes pinning **opt-in**: when a fingerprint
is configured, the connector verifies the device's leaf certificate matches it **before sending any
credentials**; when no fingerprint is set, `verify_tls=False` stays permissive (accepts the self-signed
cert, exactly as today — per the user's requirement).

## 2. TLS verification matrix (decision)

| `verify_tls` | `tls_fingerprint` | Behaviour |
|--------------|-------------------|-----------|
| `True` | (any) | Full CA verification (httpx default `verify=True`). Unchanged. |
| `False` | **set** | **Pin**: verify the leaf cert's SHA-256 fingerprint matches; mismatch → sanitized error (no creds sent). MITM-resistant. |
| `False` | unset/empty | **Permissive**: accept any cert (current behaviour). Self-signed-without-pinning keeps working. |

## 3. Design decisions

| Topic | Decision |
|-------|----------|
| When to verify | **Before the request** (a pre-flight TLS handshake) — a post-handshake check would already have leaked the API key/secret to a MITM. Pinning must gate credential transmission. |
| Where to connect | The **same SSRF-pinned IP + SNI host + port** the request uses (`validate_base_url` result) — no SSRF bypass, no DNS-rebinding window. The SSRF guard stays **byte-identical**. |
| Fingerprint format | SHA-256. Normalised on both sides: strip `:`/whitespace, lowercase, drop an optional `sha256:` prefix. Compared constant-time-ish (string equality of hex is fine; it's not a secret). |
| Mechanism | A small helper opens a TLS connection (`CERT_NONE`, `check_hostname=False`, `server_hostname=host` for SNI) to the pinned IP, reads the peer cert DER (`getpeercert(binary_form=True)`), computes `sha256`, compares to the pinned value. Mismatch → `ReachabilityError("certificate fingerprint mismatch")` (sanitized). |
| Error sanitisation | Like the rest of the connector: no upstream detail leaked; a mismatch is a generic reachability/security failure. |
| Provisioning the fingerprint | Out of scope for SEC-2 (the operator sets it). A follow-up can surface the observed fingerprint via test-connection (display, not auto-trust) or a TOFU capture — recorded as debt. |

## 4. Components

- `app/connectors/opnsense/tls_pinning.py` (new): `normalize_fingerprint(s) -> str`; `async def peer_fingerprint(host, ip, port, *, timeout) -> str`; `async def verify_pinned(host, ip, port, expected, *, timeout) -> None` (raises on mismatch).
- `app/connectors/opnsense/client.py`: `OpnsenseClient.__init__` gains `tls_fingerprint: str | None = None`; in `_request`, after `validate_base_url(...)` resolves `(pinned_ip, host, port)`, **if `not self._verify and self._fingerprint`** → `await verify_pinned(host, pinned_ip, port or 443, self._fingerprint, timeout=self._timeout)` before building the httpx client. Everything else (IP-pinning, Host/SNI, `verify=self._verify`, `follow_redirects=False`) unchanged.
- Wire `tls_fingerprint=device.tls_fingerprint` into the construction sites: `app/services/onboarding.py` (`probe_device` already has the param — pass it through), `app/api/config.py`, `app/worker.py` (4 jobs).

## 5. Security & safety

- **Credential safety:** the fingerprint is verified BEFORE the authenticated request — a mismatch aborts with no creds sent.
- **No SSRF regression:** the pre-flight uses the validated pinned IP + SNI host (same as the request); `validate_base_url` and the request path are unchanged (byte-identical SSRF guard).
- **Fail-safe default:** no fingerprint → behaviour is exactly today's (permissive when `verify_tls=False`) — self-signed setups are not broken (the user's explicit requirement).
- **Sanitized errors:** a mismatch yields a generic error (no cert/host detail).
- Note: pinning is the operator's opt-in MITM defence; without it, `verify_tls=False` is documented as "no cert verification" (unchanged risk, operator's choice).

## 6. Milestone SEC-2 breakdown (for the plan)
1. **Pinning helper** (`tls_pinning.py`: normalize + peer_fingerprint + verify_pinned) + unit tests against a **local self-signed TLS server** (correct fingerprint → ok; wrong → raises; normalisation cases).
2. **Connector integration** (`OpnsenseClient` gains `tls_fingerprint`; `_request` pre-flight verifies when `verify_tls=False` + fingerprint set; verify=True and no-fingerprint paths unchanged) + tests (mismatch → ReachabilityError before any request; permissive path unchanged; SSRF guard intact).
3. **Wire `device.tls_fingerprint`** into all construction sites + tests.
4. **Tech debt** (pre-flight opens an extra handshake per request — cache/verify-once later; fingerprint provisioning UX — TOFU/display — is a follow-up).

## 7. Definition of "Done" (SEC-2)
- With `verify_tls=False` + a pinned fingerprint, the connector verifies the device cert before sending
  credentials and aborts (sanitized) on mismatch; with no fingerprint, `verify_tls=False` still accepts a
  self-signed cert; `verify_tls=True` is unchanged CA verification. The SSRF guard is byte-identical.
  All construction sites pass the device's fingerprint. Backend suite green.

## 8. Non-goals (SEC-2) / next
- **Fingerprint provisioning UX** (display observed fingerprint on test-connection / TOFU capture) — follow-up.
- Verify-once caching (the connector builds a client per request — a known debt; the pre-flight inherits it).
- Session lifecycle + per-session CSRF token (SEC-3); MASTER_KEY rotation (later).
