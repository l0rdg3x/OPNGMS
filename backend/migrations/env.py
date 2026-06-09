import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to):
    """Esclude dall'autogenerate gli oggetti interni di TimescaleDB.

    `create_hypertable` crea automaticamente un indice `<table>_<timecol>_idx` (es.
    `metrics_time_idx`) che non è nel modello; e gli oggetti vivono in `_timescaledb_internal`.
    Senza questo filtro alembic check segnalerebbe drift spurio.
    """
    if type_ == "index" and name and name.endswith("_time_idx"):
        return False
    if getattr(obj, "schema", None) == "_timescaledb_internal":
        return False
    return True


def _db_url() -> str:
    return os.getenv("ALEMBIC_DATABASE_URL") or get_settings().database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_db_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
