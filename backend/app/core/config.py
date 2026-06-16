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
    trusted_device_enabled: bool = True  # org default for "remember this device" (admin can override)
    trusted_device_days: int = 30  # how long a trusted device skips the second factor (1..365)
    admin_database_url: str | None = None  # owner, for the worker (bypasses RLS)
    redis_url: str = "redis://localhost:6379"
    # Boot-time deploy tuning (requires restart). See .env.example "Boot-time tuning".
    worker_max_jobs: int = 10  # ARQ worker concurrency (>=1)
    db_pool_size: int = 5  # SQLAlchemy engine pool size, API + worker (>=1)
    db_max_overflow: int = 10  # SQLAlchemy pool overflow beyond pool_size (>=0)
    opnsense_http_timeout: float = 10.0  # default per-request connector timeout, seconds (>0)
    # Runtime-default firmware-poll budget (initial value; editable live from the System page).
    firmware_max_status_polls: int = 360  # max upgradestatus polls before giving up (>=1)
    firmware_poll_interval_seconds: float = 5.0  # delay between upgradestatus polls, seconds (>0)
    poll_interval_seconds: int = 60
    cors_allow_origins: str = ""  # comma-separated; empty = CORS disabled (same-origin)
    login_max_attempts: int = 5
    login_lockout_window_seconds: int = 900
    # Per-tenant-overridable data retention (worker purge reads the effective value). See app/services/retention.py.
    perimeter_retention_days: int = 30   # per-tenant-overridable; worker purge reads the effective value
    events_retention_days: int = 90      # replaces the native TimescaleDB retention policy (PR2)
    metrics_retention_days: int = 30     # replaces the native TimescaleDB retention policy (PR2)
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
    syslog_cert_dir: str = "/certs"  # shared cert volume; the worker writes the CRL under <dir>/crl/
    device_cert_days: int = 90  # device forwarding-cert lifetime (short -> bounds a stolen-key window)
    cert_renewal_window_days: int = 30  # renew a forwarding cert when not_after < now + this window
    cert_renewal_hour: int = 3  # daily UTC hour the cert-renewal cron runs
    opensearch_url: str = "http://opensearch:9200"
    log_retention_days: int = 30
    log_search_max_size: int = 200
    log_search_max_range_days: int = 31
    log_fleet_terms_size: int = 10000  # max tenants in the MSP log-fleet terms agg (no silent truncation)
    silent_alert_enabled: bool = True  # master switch for the silent-tenant detector cron
    silent_alert_after_hours: int = 6  # alert a tenant silent for longer than this (UI badge uses 1h)
    silent_alert_cron_minute: int = 0  # minute of each hour the silent-tenant detector runs
    # Catalog distribution (sub-project 2): where the app fetches versioned OPNsense catalogs.
    catalog_release_base_url: str = (
        "https://github.com/l0rdg3x/OPNGMS/releases/download/catalogs"
    )
    catalog_auto_fetch: bool = True  # fetch + cache catalogs on cache-miss (off => cache-only)
    # GeoIP distribution: where the app fetches the DB-IP Lite Country mmdb (attacker-country resolution).
    geoip_release_base_url: str = (
        "https://github.com/l0rdg3x/OPNGMS/releases/download/geoip"
    )
    geoip_auto_fetch: bool = True  # fetch + cache the geoip mmdb on cache-miss (off => cache-only)
    # WebAuthn (passkey second factor). Registration is disabled until rp_id + origin are set; these
    # are the env defaults, overridable at runtime from the System page. WebAuthn binds credentials to
    # a registrable domain (rp_id) and verifies the origin, so both must match the deployment's HTTPS URL.
    webauthn_rp_id: str = ""          # e.g. "opngms.example.com" (registrable domain, NO scheme/port)
    webauthn_rp_name: str = "OPNGMS"  # human-readable relying-party name shown by the authenticator
    webauthn_origin: str = ""         # e.g. "https://opngms.example.com" (the page origin)


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
    # The release-asset fetchers (catalog/geoip) verify SHA-256, but the fetch itself must be HTTPS so a
    # plaintext MITM can't strip/replace the manifest before verification.
    insecure = sorted(
        name
        for name, val in {
            "CATALOG_RELEASE_BASE_URL": settings.catalog_release_base_url,
            "GEOIP_RELEASE_BASE_URL": settings.geoip_release_base_url,
        }.items()
        if not val.startswith("https://")
    )
    if insecure:
        raise RuntimeError(
            "Refusing to start: " + ", ".join(insecure) + " must use https:// (release-asset fetch)."
        )
