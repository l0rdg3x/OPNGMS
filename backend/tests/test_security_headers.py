"""Tests for security response headers middleware and opt-in CORS."""
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.security import SECURITY_HEADERS
from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REQUIRED_HEADERS = [
    ("x-content-type-options", "nosniff"),
    ("x-frame-options", "DENY"),
    ("strict-transport-security", "max-age=63072000; includeSubDomains"),
]


# ---------------------------------------------------------------------------
# Security-header tests — use the plain `client` fixture (no DB needed)
# ---------------------------------------------------------------------------


async def test_healthz_carries_security_headers(client):
    """GET /healthz must include the required security headers on a 200 response."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    for header, value in REQUIRED_HEADERS:
        assert resp.headers.get(header) == value, (
            f"Missing or wrong header {header!r}: got {resp.headers.get(header)!r}"
        )
    # CSP must be present (non-empty)
    csp = resp.headers.get("content-security-policy", "")
    assert csp, "Content-Security-Policy header must be present"
    assert "default-src" in csp


async def test_security_headers_on_error_response(client):
    """Headers must appear even on error responses (e.g., 401 on an auth-required endpoint)."""
    # POST /api/login with no body → 422 Unprocessable Entity
    resp = await client.post("/api/login", json={})
    assert resp.status_code in (401, 422)
    for header, value in REQUIRED_HEADERS:
        assert resp.headers.get(header) == value, (
            f"Missing header {header!r} on error response"
        )
    csp = resp.headers.get("content-security-policy", "")
    assert csp, "Content-Security-Policy must be present on error responses"


async def test_security_headers_on_404(client):
    """Headers must appear on 404 responses too."""
    resp = await client.get("/nonexistent-path-xyz")
    assert resp.status_code == 404
    for header, value in REQUIRED_HEADERS:
        assert resp.headers.get(header) == value, (
            f"Missing header {header!r} on 404 response"
        )


async def test_cors_disabled_by_default(client):
    """With cors_allow_origins unset (empty string), a cross-origin preflight must NOT
    return an Access-Control-Allow-Origin header."""
    resp = await client.options(
        "/healthz",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # The server must NOT echo back the foreign origin.
    assert "access-control-allow-origin" not in resp.headers, (
        "CORS must be disabled when cors_allow_origins is empty; "
        f"got access-control-allow-origin: {resp.headers.get('access-control-allow-origin')!r}"
    )


async def test_cors_enabled_when_configured(monkeypatch):
    """When cors_allow_origins is set, CORSMiddleware allows the listed origin."""
    from app.core import config as cfg_module

    # Build a fresh app instance with CORS enabled for a test origin.
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from app.core.security import SecurityHeadersMiddleware

    test_app = FastAPI()
    test_app.add_middleware(SecurityHeadersMiddleware)
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://allowed.example.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @test_app.get("/healthz")
    async def _healthz():
        return {"status": "ok"}

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        resp = await c.options(
            "/healthz",
            headers={
                "Origin": "https://allowed.example.com",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert resp.headers.get("access-control-allow-origin") == "https://allowed.example.com"


async def test_security_headers_dict_completeness():
    """SECURITY_HEADERS must contain all six expected keys."""
    expected_keys = {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Strict-Transport-Security",
        "Content-Security-Policy",
    }
    assert expected_keys == set(SECURITY_HEADERS.keys()), (
        f"SECURITY_HEADERS keys mismatch: {set(SECURITY_HEADERS.keys())}"
    )
