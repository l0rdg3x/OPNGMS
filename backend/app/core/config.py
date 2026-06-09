from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str
    test_database_url: str | None = None
    session_secret: str
    master_key: str  # Fernet key urlsafe-base64 (usata dalla Milestone C)
    session_ttl_hours: int = 12
    admin_database_url: str | None = None  # owner, per il worker (bypassa RLS)
    redis_url: str = "redis://localhost:6379"
    poll_interval_seconds: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
