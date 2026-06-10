"""OPNGMS application-security regression suite

Consolidated invariant tests: one module, eight guards. Each test function
exercises exactly one security control. A test failure means a guard has been
weakened or removed — treat it as a blocker.

Invariants covered
------------------
1. CSRF enforcement  — mutation without X-OPNGMS-CSRF → 403
2. RLS cross-tenant  — app_role_api_client: tenant B cannot read tenant A devices
3. SSRF guard        — validate_base_url rejects cloud-metadata / loopback / non-HTTPS
4. Config secret redaction — build_tree redacts <password> and <privkey> values to None
5. Security headers  — GET /healthz carries X-Content-Type-Options, X-Frame-Options,
                       Content-Security-Policy, Strict-Transport-Security
6. Login rate-limit  — N+1 failed logins trigger 429 with Retry-After
7. SQL-injection allowlist — bad field raises ValueError (repo) and returns 400 (API)
8. XXE neutralised   — defusedxml-backed build_tree rejects entity payloads
"""

import json
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

# ---------------------------------------------------------------------------
# 1. CSRF enforcement
#    Mirror tests/test_csrf.py exactly.
# ---------------------------------------------------------------------------

async def _login_csrf_user(api_client):
    """Create and authenticate a throwaway superadmin user."""
    await api_client.post(
        "/api/setup", json={"email": "csrf_suite@x.io", "name": "CSRFSuite", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "csrf_suite@x.io", "password": "pw12345"})


@pytest.mark.asyncio
async def test_csrf_mutation_without_header_rejected(api_client):
    """A state-changing request without X-OPNGMS-CSRF → 403."""
    await _login_csrf_user(api_client)
    resp = await api_client.post("/api/logout")  # no CSRF header
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 2. RLS cross-tenant isolation
#    Mirror tests/test_devices_rls_api.py: under app_role_api_client (real
#    opngms_app role, RLS active) a device created in tenant A is NOT visible
#    when listing tenant B's devices.
# ---------------------------------------------------------------------------

from app.services.onboarding import ProbeResult, get_prober
from app.main import app
from tests.factories import make_tenant

CSRF = {"X-OPNGMS-CSRF": "1"}


async def _rls_setup_two_tenants(app_role_api_client, db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        a = await make_tenant(s, slug="rls_a")
        b = await make_tenant(s, slug="rls_b")
        await s.commit()
        ta, tb = a.id, b.id
    await app_role_api_client.post(
        "/api/setup", json={"email": "rls_sa@x.io", "name": "RLSSA", "password": "pw12345"}
    )

    async def _fake_prober(*ar, **kw):
        return ProbeResult(reachable=True, firmware_version="24.7", error=None)

    app.dependency_overrides[get_prober] = lambda: _fake_prober
    await app_role_api_client.post(
        "/api/login", json={"email": "rls_sa@x.io", "password": "pw12345"}
    )
    return ta, tb


@pytest.mark.asyncio
async def test_rls_device_in_tenant_a_not_visible_in_tenant_b(app_role_api_client, db_engine):
    """Under the real opngms_app role, tenant B's device list is empty when only A has devices."""
    ta, tb = await _rls_setup_two_tenants(app_role_api_client, db_engine)
    created = await app_role_api_client.post(
        f"/api/tenants/{ta}/devices",
        json={"name": "fw-rls-a", "base_url": "https://rls-a", "api_key": "k", "api_secret": "s"},
        headers=CSRF,
    )
    assert created.status_code == 201
    la = await app_role_api_client.get(f"/api/tenants/{ta}/devices")
    assert any(d["name"] == "fw-rls-a" for d in la.json())
    lb = await app_role_api_client.get(f"/api/tenants/{tb}/devices")
    assert lb.json() == []


# ---------------------------------------------------------------------------
# 3. SSRF guard
#    Import the same validator used in tests/test_url_safety.py.
# ---------------------------------------------------------------------------

from app.connectors.opnsense.url_safety import UnsafeUrlError, validate_base_url


def test_ssrf_cloud_metadata_blocked():
    """Cloud metadata endpoint (169.254.169.254 / link-local) must be rejected."""
    with pytest.raises(UnsafeUrlError):
        validate_base_url("https://169.254.169.254/latest/meta-data/")


def test_ssrf_loopback_blocked():
    """Loopback addresses must be rejected."""
    with pytest.raises(UnsafeUrlError):
        validate_base_url("https://127.0.0.1")


def test_ssrf_ipv6_loopback_blocked():
    """IPv6 loopback must be rejected."""
    with pytest.raises(UnsafeUrlError):
        validate_base_url("https://[::1]")


def test_ssrf_non_https_blocked():
    """Plain HTTP URLs must be rejected (non-HTTPS)."""
    with pytest.raises(UnsafeUrlError):
        validate_base_url("http://203.0.113.10")


def test_ssrf_private_rfc1918_allowed():
    """RFC1918 addresses are allowed (OPNsense lives on management networks)."""
    ip, host, _port = validate_base_url("https://10.0.0.5")
    assert ip == "10.0.0.5"


# ---------------------------------------------------------------------------
# 4. Config secret redaction
#    Mirror tests/test_config_model.py: build_tree must redact <password> and
#    <privkey> values to None (never emit secrets in the model output).
# ---------------------------------------------------------------------------

from app.services.config_model import build_tree


def test_config_password_leaf_redacted():
    """<password> value must be None in the model output (never the secret string)."""
    xml = (
        "<opnsense><system>"
        "<user><name>root</name><password>SECRET</password></user>"
        "</system></opnsense>"
    )
    root = build_tree(xml)
    blob = json.dumps(root)
    assert "SECRET" not in blob, "Secret value must not appear anywhere in the model output"
    # Locate the password node and confirm it is explicitly redacted
    system = root["children"][0]
    user = system["children"][0]
    pw_nodes = [c for c in user["children"] if c["tag"] == "password"]
    assert pw_nodes, "password node must be present in the tree"
    pw = pw_nodes[0]
    assert pw["sensitive"] is True
    assert pw["value"] is None


def test_config_privkey_leaf_redacted():
    """<privkey> value must be None in the model output (never the secret string)."""
    xml = "<opnsense><cert><privkey>SECRET2</privkey></cert></opnsense>"
    root = build_tree(xml)
    blob = json.dumps(root)
    assert "SECRET2" not in blob, "Private key must not appear anywhere in the model output"
    privkey = root["children"][0]["children"][0]
    assert privkey["tag"] == "privkey"
    assert privkey["sensitive"] is True
    assert privkey["value"] is None


# ---------------------------------------------------------------------------
# 5. Security headers
#    Mirror tests/test_security_headers.py: GET /healthz must carry the four
#    required headers.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_headers_present_on_healthz(client):
    """GET /healthz must carry the four core security headers."""
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.headers.get("x-content-type-options") == "nosniff"
    assert resp.headers.get("x-frame-options") == "DENY"
    csp = resp.headers.get("content-security-policy", "")
    assert csp and "default-src" in csp, "Content-Security-Policy must be present and non-trivial"
    sts = resp.headers.get("strict-transport-security", "")
    assert sts and "max-age" in sts, "Strict-Transport-Security must be present with max-age"


# ---------------------------------------------------------------------------
# 6. Login rate-limit
#    Mirror tests/test_login_ratelimit.py: N+1 failed logins → 429.
#    Reset the limiter key before and after to avoid cross-test pollution.
# ---------------------------------------------------------------------------

from app.api.auth import login_limiter

_RL_EMAIL = "sec_suite_rl@x.io"
_RL_IP = "127.0.0.1"


def _rl_key() -> str:
    return f"{_RL_EMAIL}|{_RL_IP}"


@pytest.fixture(autouse=False)
def _reset_rl_key():
    login_limiter.reset(_rl_key())
    yield
    login_limiter.reset(_rl_key())


@pytest.mark.asyncio
async def test_login_rate_limit_triggers_429(_reset_rl_key, api_client):
    """After 5 failed logins the 6th must return 429 with a Retry-After header."""
    await api_client.post(
        "/api/setup",
        json={"email": _RL_EMAIL, "name": "RLSuite", "password": "pw12345"},
    )
    for attempt in range(5):
        r = await api_client.post(
            "/api/login", json={"email": _RL_EMAIL, "password": "WRONG"}
        )
        assert r.status_code == 401, f"attempt {attempt + 1}: expected 401, got {r.status_code}"
    r6 = await api_client.post(
        "/api/login", json={"email": _RL_EMAIL, "password": "WRONG"}
    )
    assert r6.status_code == 429
    assert "Retry-After" in r6.headers


# ---------------------------------------------------------------------------
# 7. SQL-injection field allowlist
#    Mirror tests/test_event_repository.py and tests/test_events_api.py:
#    (a) EventRepository.top with a malicious field raises ValueError (repo layer)
#    (b) GET /events/top?field=<injection> returns 400 (API layer)
# ---------------------------------------------------------------------------

from app.repositories.event import EventRepository


@pytest.mark.asyncio
async def test_sql_injection_field_raises_value_error(db_engine, two_tenants):
    """EventRepository.top must raise ValueError for a non-allowlisted field."""
    tenant_a, _ = two_tenants
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        with pytest.raises(ValueError, match="field not allowed"):
            await EventRepository(s, tenant_a).top(
                field="tenant_id; DROP TABLE events",
                source=None,
                frm=None,
                to=None,
                limit=10,
            )


@pytest.mark.asyncio
async def test_sql_injection_field_returns_400_via_api(api_client, db_engine):
    """GET /events/top with an injection string as `field` must return 400 from the API."""
    # Set up a tenant + auth so the endpoint can be reached
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        t = await make_tenant(s, slug="inj_tenant")
        await s.commit()
        tid = t.id
    await api_client.post(
        "/api/setup", json={"email": "inj_sa@x.io", "name": "InjSA", "password": "pw12345"}
    )
    await api_client.post("/api/login", json={"email": "inj_sa@x.io", "password": "pw12345"})
    r = await api_client.get(
        f"/api/tenants/{tid}/events/top",
        params={"field": "tenant_id; DROP TABLE events"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# 8. XXE neutralised
#    build_tree uses defusedxml.ElementTree.fromstring, which raises on any
#    DOCTYPE / entity declaration. Assert both an external-entity payload and a
#    billion-laughs payload are rejected (raise) rather than expanded.
# ---------------------------------------------------------------------------

def test_xxe_external_entity_payload_rejected():
    """A DOCTYPE with an external entity must be rejected by the config parser."""
    xxe_payload = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE opnsense [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<opnsense><x>&xxe;</x></opnsense>"
    )
    with pytest.raises(Exception):
        build_tree(xxe_payload)


def test_xxe_billion_laughs_payload_rejected():
    """A billion-laughs (entity-expansion) payload must be rejected by the config parser."""
    bomb = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY a "x"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
        "<opnsense><x>&b;</x></opnsense>"
    )
    with pytest.raises(Exception):
        build_tree(bomb)
