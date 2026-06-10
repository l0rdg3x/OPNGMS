"""Tests for login rate-limiting and failed-login audit (SEC-1 Task 2)."""
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.api.auth import login_limiter
from app.models.audit import AuditLog

# Use a unique email per-test (or reset the limiter) to avoid cross-test pollution.
_TEST_EMAIL = "ratelimit_test@x.io"
_TEST_IP = "127.0.0.1"  # default client IP used by httpx ASGITransport


def _key(email: str = _TEST_EMAIL, ip: str = _TEST_IP) -> str:
    return f"{email.lower()}|{ip}"


@pytest.fixture(autouse=True)
def reset_limiter():
    """Reset the shared in-process limiter before each test in this module."""
    login_limiter.reset(_key())
    yield
    login_limiter.reset(_key())


async def _setup_user(api_client, email: str = _TEST_EMAIL, password: str = "pw12345"):
    """Create the first user via /api/setup (superadmin)."""
    r = await api_client.post(
        "/api/setup", json={"email": email, "name": "RLTest", "password": password}
    )
    # 200 on first call; ignore if already created (200 or 409 depending on impl)
    return r


async def test_fifth_wrong_attempt_is_401_sixth_is_429(api_client, db_engine):
    """5 wrong-password POSTs → 401 each; the 6th → 429 with Retry-After."""
    await _setup_user(api_client)

    for i in range(5):
        r = await api_client.post(
            "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
        )
        assert r.status_code == 401, f"attempt {i + 1} expected 401, got {r.status_code}"

    r6 = await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
    )
    assert r6.status_code == 429
    assert "Retry-After" in r6.headers
    retry_after = int(r6.headers["Retry-After"])
    assert retry_after >= 1


async def test_429_fires_before_auth_so_no_extra_audit_row(api_client, db_engine):
    """The 6th attempt hits 429 BEFORE authenticating (no extra audit row for a 429)."""
    await _setup_user(api_client)

    for _ in range(5):
        await api_client.post(
            "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
        )

    # Count audit rows before the 429 attempt
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        before = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "auth.login.failed")
            )
        ).scalars().all()
        count_before = len(before)

    # 6th attempt → 429 (blocked before auth, so no new audit row)
    r = await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
    )
    assert r.status_code == 429

    async with factory() as s:
        after = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "auth.login.failed")
            )
        ).scalars().all()
        count_after = len(after)

    assert count_after == count_before, "429 should not add an extra audit row"


async def test_correct_login_resets_counter(api_client, db_engine):
    """A successful login resets the counter so the next wrong attempt is 401, not 429."""
    await _setup_user(api_client)

    # Exhaust 4 out of 5 attempts
    for _ in range(4):
        await api_client.post(
            "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
        )

    # Correct login resets the counter
    r_ok = await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "pw12345"}
    )
    assert r_ok.status_code == 200

    # After reset, a wrong attempt is 401 again (not 429)
    r_wrong = await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
    )
    assert r_wrong.status_code == 401


async def test_failed_login_writes_audit_row(api_client, db_engine):
    """A failed login attempt writes an auth.login.failed audit row with the email."""
    await _setup_user(api_client)

    await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
    )

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "auth.login.failed")
            )
        ).scalars().all()

    assert len(rows) >= 1
    row = rows[-1]
    assert row.action == "auth.login.failed"
    assert row.actor_user_id is None
    assert row.details.get("email") == _TEST_EMAIL


async def test_failed_login_does_not_log_password(api_client, db_engine):
    """The failed-login audit row must NOT contain the password in details."""
    await _setup_user(api_client)

    await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "supersecretpassword"}
    )

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        rows = (
            await s.execute(
                select(AuditLog).where(AuditLog.action == "auth.login.failed")
            )
        ).scalars().all()

    assert len(rows) >= 1
    for row in rows:
        details_str = str(row.details)
        assert "supersecretpassword" not in details_str


async def test_successful_login_after_full_reset_via_limiter(api_client, db_engine):
    """With attempts < max, a correct-password login succeeds (200)."""
    await _setup_user(api_client)

    # 2 wrong attempts (well below max)
    for _ in range(2):
        await api_client.post(
            "/api/login", json={"email": _TEST_EMAIL, "password": "WRONG"}
        )

    r = await api_client.post(
        "/api/login", json={"email": _TEST_EMAIL, "password": "pw12345"}
    )
    assert r.status_code == 200
