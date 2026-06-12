import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# The literal substring every secret placeholder in .env.example carries. The startup guard rejects
# any secret that still contains it, so a deployment cannot silently run on the shipped defaults.
_PLACEHOLDER = "change-me"


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
    mfa_pending_ttl_minutes: int = 5  # short-lived mfa_pending challenge window (password ok, awaiting TOTP)
    admin_database_url: str | None = None  # owner, for the worker (bypasses RLS)
    redis_url: str = "redis://localhost:6379"
    poll_interval_seconds: int = 60
    cors_allow_origins: str = ""  # comma-separated; empty = CORS disabled (same-origin)
    login_max_attempts: int = 5
    login_lockout_window_seconds: int = 900
    # Worker cron cadences (configurable; see app/worker.py).
    ingest_every_minutes: int = 5  # event ingest cadence (1..30)
    config_backup_hour: int = 3  # daily config backup hour (UTC, 0..23)
    report_weekday: str = "mon"  # weekly report day (mon..sun)  # (legacy: superseded by per-schedule weekday/hour in report_schedule)
    report_hour: int = 4  # weekly report hour (UTC, 0..23)  # (legacy: superseded by per-schedule weekday/hour in report_schedule)
    session_cleanup_minute: int = 0  # expired-session cleanup minute-of-hour (hourly)
    live_push_enabled: bool = False  # master switch: real config push (default OFF -> dry-run)
    sweep_every_minutes: int = 5  # orphaned-action sweeper cadence (1..30)
    orphan_grace_minutes: int = 5  # don't touch a scheduled row until this overdue
    max_reenqueue_attempts: int = 5  # give up an orphan after this many device-free re-enqueues
    syslog_receiver_host: str = "logs.opngms.local"  # public name/IP devices ship logs to
    syslog_tls_port: int = 6514
    cert_renewal_window_days: int = 30  # renew a forwarding cert when not_after < now + this window
    cert_renewal_hour: int = 3  # daily UTC hour the cert-renewal cron runs
    opensearch_url: str = "http://opensearch:9200"
    log_retention_days: int = 30
    log_search_max_size: int = 200
    log_search_max_range_days: int = 31
    log_fleet_terms_size: int = 10000  # max tenants in the MSP log-fleet terms agg (no silent truncation)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def assert_secure_secrets(settings: Settings) -> None:
    """Fail closed if any secret still holds an `.env.example` placeholder.

    Forces operators off the shipped default credentials before the app will start. Real values
    (dev/test/CI included) never contain the placeholder, so this is a no-op for them. The DB password
    is env-driven (DATABASE_URL ↔ APP_ROLE_PASSWORD, ADMIN_DATABASE_URL ↔ POSTGRES_PASSWORD); this
    guard ensures it is actually set rather than left at the template value.
    """
    candidates = {
        "DATABASE_URL": settings.database_url,
        "ADMIN_DATABASE_URL": settings.admin_database_url or "",
        "SESSION_SECRET": settings.session_secret,
        "MASTER_KEY": settings.master_key,
        "APP_ROLE_PASSWORD": os.getenv("APP_ROLE_PASSWORD", ""),
    }
    bad = sorted(name for name, val in candidates.items() if _PLACEHOLDER in (val or ""))
    if bad:
        raise RuntimeError(
            "Refusing to start: unedited .env.example placeholder(s) in " + ", ".join(bad) + ". "
            "Set strong, unique values in your .env — keep DATABASE_URL's password matched to "
            "APP_ROLE_PASSWORD, and ADMIN_DATABASE_URL's to POSTGRES_PASSWORD. "
            "See the README 'Deployment' / 'Configuration' sections."
        )
