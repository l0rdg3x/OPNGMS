from app.core.config import Settings


def test_settings_load_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/opngms")
    monkeypatch.setenv("SESSION_SECRET", "session-secret")
    monkeypatch.setenv("MASTER_KEY", "bWFzdGVyLWtleS0zMi1ieXRlcy1sb25nLXh4eHh4eHg=")
    settings = Settings()
    assert settings.database_url.startswith("postgresql+asyncpg://")
    assert settings.session_secret == "session-secret"
    assert settings.session_ttl_hours == 12  # default
