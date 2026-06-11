import pytest

from app.core.config import Settings, assert_secure_secrets

_REAL = dict(
    database_url="postgresql+asyncpg://opngms_app:realpw@db:5432/opngms",
    admin_database_url="postgresql+asyncpg://opngms:realpw@db:5432/opngms",
    session_secret="a-real-random-session-secret",
    master_key="a-real-fernet-key",
)


def _settings(**over) -> Settings:
    return Settings(**{**_REAL, **over})


def test_guard_passes_with_real_values(monkeypatch):
    monkeypatch.setenv("APP_ROLE_PASSWORD", "realpw")
    assert_secure_secrets(_settings())  # no raise


def test_guard_passes_when_app_role_password_unset(monkeypatch):
    monkeypatch.delenv("APP_ROLE_PASSWORD", raising=False)
    assert_secure_secrets(_settings())  # empty -> not a placeholder


@pytest.mark.parametrize("field,val", [
    ("database_url", "postgresql+asyncpg://opngms_app:change-me-strong-app-password@db:5432/opngms"),
    ("admin_database_url", "postgresql+asyncpg://opngms:change-me-strong-db-password@db:5432/opngms"),
    ("session_secret", "change-me-random-session-secret"),
    ("master_key", "change-me-fernet-key"),
])
def test_guard_rejects_placeholder_field(monkeypatch, field, val):
    monkeypatch.setenv("APP_ROLE_PASSWORD", "realpw")
    with pytest.raises(RuntimeError, match=field.upper().replace("_", "_")):
        assert_secure_secrets(_settings(**{field: val}))


def test_guard_rejects_placeholder_app_role_password(monkeypatch):
    monkeypatch.setenv("APP_ROLE_PASSWORD", "change-me-strong-app-password")
    with pytest.raises(RuntimeError, match="APP_ROLE_PASSWORD"):
        assert_secure_secrets(_settings())
