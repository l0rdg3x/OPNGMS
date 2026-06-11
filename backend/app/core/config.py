from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str | None = None
    session_secret: str
    master_key: str  # Fernet key urlsafe-base64 (used by Milestone C)
    master_key_old_keys: str = ""  # comma-separated retired Fernet keys, decryption-only (rotation)
    session_ttl_hours: int = 12
    session_idle_minutes: int = 120  # sliding/idle timeout, alongside the absolute session_ttl_hours
    admin_database_url: str | None = None  # owner, for the worker (bypasses RLS)
    redis_url: str = "redis://localhost:6379"
    poll_interval_seconds: int = 60
    cors_allow_origins: str = ""  # comma-separated; empty = CORS disabled (same-origin)
    login_max_attempts: int = 5
    login_lockout_window_seconds: int = 900
    # Worker cron cadences (configurable; see app/worker.py).
    ingest_every_minutes: int = 5  # event ingest cadence (1..30)
    config_backup_hour: int = 3  # daily config backup hour (UTC, 0..23)
    report_weekday: str = "mon"  # weekly report day (mon..sun)
    report_hour: int = 4  # weekly report hour (UTC, 0..23)
    session_cleanup_minute: int = 0  # expired-session cleanup minute-of-hour (hourly)
    live_push_enabled: bool = False  # master switch: real config push (default OFF -> dry-run)


@lru_cache
def get_settings() -> Settings:
    return Settings()
