import uuid
from datetime import datetime, timedelta, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.core.config import get_settings
from app.models.device import Device
from app.services.alerting import evaluate_alerts
from app.services.config_backup import backup_config
from app.services.config_push import apply_change
from app.services.ingest import ingest_events
from app.services.monitoring import collect_and_store


def _owner_url() -> str:
    s = get_settings()
    return s.admin_database_url or s.database_url


async def enqueue_device_polls(ctx: dict) -> int:
    """Cron: enqueue a poll_device for each device. Returns the number enqueued."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("poll_device", str(device_id))
    return len(ids)


async def poll_device(ctx: dict, device_id: str) -> str:
    """Job: poll a single device and save metrics+status."""
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
    """Cron: enqueue an ingest_device_events for each device."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("ingest_device_events", str(device_id))
    return len(ids)


async def ingest_device_events(ctx: dict, device_id: str) -> int:
    """Job: ingest the events (IDS) of a single device."""
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


async def enqueue_config_backups(ctx: dict) -> int:
    """Cron: enqueue a config backup for every device."""
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    async with factory() as session:
        ids = (await session.execute(select(Device.id))).scalars().all()
    for device_id in ids:
        await redis.enqueue_job("backup_device_config", str(device_id))
    return len(ids)


async def backup_device_config(ctx: dict, device_id: str) -> bool:
    """Job: back up a single device's config (dedup-on-change)."""
    factory = ctx["session_factory"]
    async with factory() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        if device is None:
            return False
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        created = await backup_config(session, device, client)
        await session.commit()
        return created


async def apply_config_change(ctx: dict, change_id: str) -> str:
    """Job: apply a scheduled config change (dry-run), staleness-guarded + audited."""
    from app.models.config_change import ConfigChange
    from app.services.audit import AuditService

    factory = ctx["session_factory"]
    async with factory() as session:
        change = await session.get(ConfigChange, uuid.UUID(change_id))
        if change is None:
            return "missing"
        device = await session.get(Device, change.device_id)
        if device is None:
            return "missing-device"
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
        )
        status = await apply_change(
            session, change, client, now=datetime.now(timezone.utc)
        )
        await AuditService(session).record(
            actor_user_id=change.created_by,
            tenant_id=change.tenant_id,
            action="config.change.apply",
            target_type="config_change",
            target_id=str(change.id),
            ip=None,
            details={"status": status},
        )
        await session.commit()
        # Refresh the snapshot so the next change's baseline reflects reality.
        await ctx["redis"].enqueue_job("backup_device_config", str(change.device_id))
        return status


async def generate_tenant_report(ctx: dict, tenant_id: str, frm: str, to: str, kind: str) -> str:
    """Job: build a report for a tenant + range and store it. Runs as owner; the aggregator's explicit
    tenant_id filters scope the data (RLS is bypassed for the owner, like the poller)."""
    from app.models.tenant import Tenant
    from app.repositories.generated_report import GeneratedReportRepository
    from app.services.reporting.service import ReportService

    factory = ctx["session_factory"]
    frm_dt, to_dt = datetime.fromisoformat(frm), datetime.fromisoformat(to)
    async with factory() as session:
        tenant = await session.get(Tenant, uuid.UUID(tenant_id))
        if tenant is None:
            return "missing-tenant"
        pdf = await ReportService(session, uuid.UUID(tenant_id)).build_report(
            tenant_name=tenant.name, frm=frm_dt, to=to_dt
        )
        await GeneratedReportRepository(session, uuid.UUID(tenant_id)).create(
            kind=kind, period_from=frm_dt, period_to=to_dt, created_by=None, pdf=pdf
        )
        await session.commit()
        return "stored"


def _prior_month(now: datetime) -> tuple[datetime, datetime]:
    first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_start = (first_this - timedelta(days=1)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return prev_start, first_this


async def enqueue_scheduled_reports(ctx: dict) -> int:
    """Cron: enqueue a monthly report for every active tenant (prior calendar month)."""
    from app.models.tenant import Tenant

    factory = ctx["session_factory"]
    redis = ctx["redis"]
    frm, to = _prior_month(datetime.now(timezone.utc))
    async with factory() as session:
        ids = (await session.execute(select(Tenant.id).where(Tenant.status == "active"))).scalars().all()
    for tid in ids:
        await redis.enqueue_job("generate_tenant_report", str(tid), frm.isoformat(), to.isoformat(), "scheduled")
    return len(ids)


async def on_startup(ctx: dict) -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)


async def on_shutdown(ctx: dict) -> None:
    await ctx["engine"].dispose()


class WorkerSettings:
    functions = [poll_device, ingest_device_events, backup_device_config, apply_config_change, generate_tenant_report]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),  # metrics, every minute at second 0
        cron(enqueue_event_ingests, minute=set(range(0, 60, 5))),  # events, every 5 minutes
        cron(enqueue_config_backups, hour={3}, minute={0}),  # config, daily ~03:00
        cron(enqueue_scheduled_reports, day={1}, hour={4}, minute={0}),  # monthly reports, 1st of month ~04:00
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
