"""Tests for the TLS fingerprint pre-flight integration in OpnsenseClient._request.

The pre-flight calls verify_pinned BEFORE the httpx request so credentials are never
sent on a pin mismatch.  respx is used to assert whether an HTTP request was/wasn't made.
"""
import httpx
import pytest
import respx

import app.connectors.opnsense.tls_pinning as tls_pinning_module
from app.connectors.opnsense.client import OpnsenseClient, ReachabilityError
from app.connectors.opnsense.tls_pinning import PinMismatchError

# 203.0.113.0/24 is TEST-NET-3 — passes the SSRF guard (not loopback/link-local/etc.)
BASE = "https://203.0.113.10"
FW_URL = f"{BASE}/api/core/firmware/status"

# validate_base_url("https://203.0.113.10") returns ("203.0.113.10", "203.0.113.10", None)
# so the pre-flight is called with: host="203.0.113.10", pinned_ip="203.0.113.10", port=443
EXPECTED_HOST = "203.0.113.10"
EXPECTED_PINNED_IP = "203.0.113.10"
EXPECTED_PORT = 443
FINGERPRINT = "ab" * 32  # 64-char hex string


# ---------------------------------------------------------------------------
# Test 1: Mismatch aborts before any HTTP request — credentials never sent
# ---------------------------------------------------------------------------


@respx.mock
async def test_mismatch_aborts_before_request(monkeypatch):
    """A PinMismatchError from verify_pinned must raise ReachabilityError
    and the respx router must have recorded ZERO calls (creds not sent)."""

    async def raise_mismatch(host, ip, port, expected, *, timeout):
        raise PinMismatchError("fingerprint did not match")

    monkeypatch.setattr(tls_pinning_module, "verify_pinned", raise_mismatch)

    # Register a catch-all route — it must NOT be called.
    respx.get(FW_URL).mock(return_value=httpx.Response(200, json={"product_version": "24.1"}))

    client = OpnsenseClient(BASE, "key", "secret", verify_tls=False, tls_fingerprint=FINGERPRINT)
    with pytest.raises(ReachabilityError, match="fingerprint mismatch"):
        await client.test_connection()

    assert respx.calls.call_count == 0, "HTTP request was made despite pin mismatch — credentials exposed!"


# ---------------------------------------------------------------------------
# Test 2: Matching fingerprint — request proceeds and verify_pinned is called
# ---------------------------------------------------------------------------


@respx.mock
async def test_match_proceeds_and_verify_pinned_called(monkeypatch):
    """When verify_pinned does not raise, the request must proceed and respx records it."""
    calls: list[tuple] = []

    async def spy_ok(host, ip, port, expected, *, timeout):
        calls.append((host, ip, port, expected))

    monkeypatch.setattr(tls_pinning_module, "verify_pinned", spy_ok)

    respx.get(FW_URL).mock(
        return_value=httpx.Response(200, json={"product_version": "24.7.5"})
    )

    client = OpnsenseClient(BASE, "key", "secret", verify_tls=False, tls_fingerprint=FINGERPRINT)
    version = await client.test_connection()
    assert version == "24.7.5"

    # respx recorded exactly one HTTP call
    assert respx.calls.call_count == 1

    # verify_pinned was called once with the correct positional args
    assert len(calls) == 1
    host_arg, ip_arg, port_arg, fp_arg = calls[0]
    assert host_arg == EXPECTED_HOST
    assert ip_arg == EXPECTED_PINNED_IP
    assert port_arg == EXPECTED_PORT
    assert fp_arg == FINGERPRINT


# ---------------------------------------------------------------------------
# Test 3: No fingerprint + verify_tls=False → permissive (verify_pinned NOT called)
# ---------------------------------------------------------------------------


@respx.mock
async def test_no_fingerprint_skips_pinning(monkeypatch):
    """When tls_fingerprint=None the pre-flight must be skipped entirely."""
    called: list[bool] = []

    async def must_not_be_called(*args, **kwargs):
        called.append(True)
        raise AssertionError("verify_pinned was called but tls_fingerprint is None")

    monkeypatch.setattr(tls_pinning_module, "verify_pinned", must_not_be_called)

    respx.get(FW_URL).mock(return_value=httpx.Response(200, json={"product_version": "24.1"}))

    client = OpnsenseClient(BASE, "key", "secret", verify_tls=False, tls_fingerprint=None)
    version = await client.test_connection()
    assert version == "24.1"
    assert not called, "verify_pinned was called despite tls_fingerprint=None"


# ---------------------------------------------------------------------------
# Test 4: verify_tls=True → CA path, verify_pinned NOT called
# ---------------------------------------------------------------------------


@respx.mock
async def test_verify_tls_true_skips_pinning(monkeypatch):
    """When verify_tls=True the pre-flight must be skipped (CA verification path is unchanged)."""
    called: list[bool] = []

    async def must_not_be_called(*args, **kwargs):
        called.append(True)
        raise AssertionError("verify_pinned was called but verify_tls=True")

    monkeypatch.setattr(tls_pinning_module, "verify_pinned", must_not_be_called)

    respx.get(FW_URL).mock(return_value=httpx.Response(200, json={"product_version": "24.1"}))

    client = OpnsenseClient(BASE, "key", "secret", verify_tls=True, tls_fingerprint=FINGERPRINT)
    version = await client.test_connection()
    assert version == "24.1"
    assert not called, "verify_pinned was called despite verify_tls=True"
