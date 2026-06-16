import inspect

from fastapi.routing import APIRoute

from app.main import app

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}

# Reads performed via POST (carry a body) — genuinely no state change, so no audit expected.
EXEMPT = {
    ("POST", "/api/tenants/{tenant_id}/devices/{device_id}/firmware/check"),
    ("POST", "/api/tenants/{tenant_id}/logs/search"),
    ("POST", "/api/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/preview"),
    ("POST", "/api/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/preview"),
    # ("POST", "/api/me/mfa/setup"),  # not exempt: persists a pending TOTP secret -> mfa.setup_start
    # WebAuthn "begin" routes only mint a single-use challenge bound to the session row (no durable
    # credential / state change); the matching "complete" routes audit the actual mutation.
    ("POST", "/api/me/mfa/webauthn/register/begin"),
    ("POST", "/api/login/webauthn/begin"),
}
# Routes that audit inside a service they call, not inline — explicit so it's a reviewed choice.
AUDITED_INDIRECT: set[tuple[str, str]] = set()


def _audits_inline(endpoint) -> bool:
    try:
        src = inspect.getsource(endpoint)
    except (OSError, TypeError):
        return False
    return ".record(" in src


def test_every_mutating_route_is_audited_or_allowlisted():
    missing = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        methods = route.methods & MUTATING
        if not methods:
            continue
        for m in methods:
            key = (m, route.path)
            if key in EXEMPT or key in AUDITED_INDIRECT:
                continue
            if not _audits_inline(route.endpoint):
                missing.append(key)
    assert not missing, (
        "Mutating routes with no audit.record() and not allowlisted:\n"
        + "\n".join(f"  {m} {p}" for m, p in sorted(missing))
        + "\nAdd an AuditService(...).record(...) call, or add to EXEMPT/AUDITED_INDIRECT with a reason."
    )
