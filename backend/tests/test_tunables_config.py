import pathlib

from app.core.config import Settings


def _settings(**env):
    # _env_file=None: ignore any real .env so defaults are exercised deterministically.
    return Settings(_env_file=None, database_url="x", session_secret="x", master_key="x", **env)


def test_boot_time_defaults_match_current_behavior():
    s = _settings()
    assert s.worker_max_jobs == 10
    assert s.db_pool_size == 5
    assert s.db_max_overflow == 10
    assert s.opnsense_http_timeout == 10.0


def test_runtime_default_fields_match_firmware_constants():
    # The two firmware-poll runtime defaults must mirror today's firmware_action.py module constants.
    from app.services import firmware_action

    s = _settings()
    assert s.firmware_max_status_polls == firmware_action.MAX_STATUS_POLLS == 360
    assert s.firmware_poll_interval_seconds == firmware_action.POLL_INTERVAL == 5.0


def test_boot_time_overrides_from_env(monkeypatch):
    monkeypatch.setenv("WORKER_MAX_JOBS", "4")
    monkeypatch.setenv("DB_POOL_SIZE", "20")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "0")
    monkeypatch.setenv("OPNSENSE_HTTP_TIMEOUT", "7.5")
    s = _settings()
    assert (s.worker_max_jobs, s.db_pool_size, s.db_max_overflow, s.opnsense_http_timeout) == (
        4,
        20,
        0,
        7.5,
    )


def test_make_engine_applies_pool_settings(monkeypatch):
    monkeypatch.setenv("DB_POOL_SIZE", "7")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "3")
    from app.core import config, db

    config.get_settings.cache_clear()
    try:
        engine = db.make_engine("postgresql+asyncpg://u:p@localhost/x")
        assert engine.pool.size() == 7
        assert engine.pool._max_overflow == 3
    finally:
        config.get_settings.cache_clear()


def test_env_example_documents_boot_time_keys():
    text = pathlib.Path(__file__).resolve().parents[2].joinpath(".env.example").read_text()
    for key in ("WORKER_MAX_JOBS", "DB_POOL_SIZE", "DB_MAX_OVERFLOW", "OPNSENSE_HTTP_TIMEOUT"):
        assert key in text, f"{key} missing from .env.example"
