import uuid
from datetime import datetime, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.core.config import get_settings
from app.models.device import Device
from app.services.alerting import evaluate_alerts
from app.services.ingest import ingest_events
from app.services.monitoring import collect_and_store


def _owner_url() -> str:
    s = get_settings()
    return s.admin_database_url or s.database_url


async def enqueue_device_polls(ctx: dict) -> int:
    """Cron: accoda un poll_device per ogni device. Ritorna il numero accodato."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("poll_device", str(device_id))
    return len(ids)


async def poll_device(ctx: dict, device_id: str) -> str:
    """Job: pollla un singolo device e salva metriche+stato."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return "missing"
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        state = await collect_and_store(session, device, client, now=datetime.now(timezone.utc))
        await evaluate_alerts(session, device, state)
        await session.commit()
        return device.status


async def enqueue_event_ingests(ctx: dict) -> int:
    """Cron: accoda un ingest_device_events per ogni device."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("ingest_device_events", str(device_id))
    return len(ids)


async def ingest_device_events(ctx: dict, device_id: str) -> int:
    """Job: ingerisce gli eventi (IDS) di un singolo device."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return 0
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        n = await ingest_events(session, device, client, now=datetime.now(timezone.utc))
        await session.commit()
        return n


async def on_startup(ctx: dict) -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)


async def on_shutdown(ctx: dict) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [poll_device, ingest_device_events]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),  # metriche, ogni minuto al secondo 0
        cron(enqueue_event_ingests, minute=set(range(0, 60, 5))),  # eventi, ogni 5 minuti
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
