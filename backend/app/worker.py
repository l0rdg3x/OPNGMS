import uuid
from datetime import UTC, datetime, timedelta

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.firewall_rule_kind  # noqa: F401  — registers firewall_rule kind at worker-process startup
import app.services.ids_kind  # noqa: F401  — registers suricata_ruleset kind at worker-process startup
import app.services.monit_kind  # noqa: F401  — registers monit_test kind at startup
import app.services.setting_kind  # noqa: F401  — registers opnsense_setting kind at worker-process startup
from app.connectors.opnsense.client import OpnsenseClient
from app.core import crypto
from app.core.config import get_settings
from app.models.device import Device
from app.services.action_sweeper import decide_orphan
from app.services.alerting import evaluate_alerts
from app.services.config_backup import backup_config
from app.services.config_push import _advisory_key, apply_change
from app.services.email.smtp import EmailSendError, send_report_email
from app.services.ingest import ingest_events
from app.services.monitoring import collect_and_store
from app.services.report_schedule import ON_DEMAND, report_window
from app.services.report_schedule import next_run_at as _next_run_at


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
            tls_fingerprint=device.tls_fingerprint,
        )
        state = await collect_and_store(session, device, client, now=datetime.now(UTC))
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
            tls_fingerprint=device.tls_fingerprint,
        )
        n = await ingest_events(session, device, client, now=datetime.now(UTC))
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
            tls_fingerprint=device.tls_fingerprint,
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
            tls_fingerprint=device.tls_fingerprint,
        )
        status = await apply_change(
            session, change, client, now=datetime.now(UTC)
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


async def run_firmware_action(ctx: dict, action_id: str) -> str:
    """Job: run a scheduled/now firmware action against a device."""
    from app.models.firmware_action import FirmwareAction
    from app.services.audit import AuditService
    from app.services.firmware_action import run_firmware_action as _run

    factory = ctx["session_factory"]
    async with factory() as session:
        action = await session.get(FirmwareAction, uuid.UUID(action_id))
        if action is None:
            return "missing"
        device = await session.get(Device, action.device_id)
        if device is None:
            return "missing-device"
        client = OpnsenseClient(
            device.base_url,
            crypto.decrypt(device.api_key_enc),
            crypto.decrypt(device.api_secret_enc),
            verify_tls=device.verify_tls,
            tls_fingerprint=device.tls_fingerprint,
        )
        device_id = action.device_id
        status = await _run(session, action, client, now=datetime.now(UTC))
        await AuditService(session).record(
            actor_user_id=action.created_by,
            tenant_id=action.tenant_id,
            action="device.firmware.action",
            target_type="firmware_action",
            target_id=str(action.id),
            ip=None,
            details={"kind": action.kind, "status": status},
        )
        await session.commit()
        if status == "done":
            await ctx["redis"].enqueue_job("backup_device_config", str(device_id))
        return status


async def sweep_orphaned_actions(ctx: dict) -> dict:
    """Cron: re-enqueue scheduled config/firmware actions dropped by a device lock-miss.

    For each overdue scheduled row, try the device advisory lock: if a real op holds it, skip; if
    free, the row is a genuine orphan — re-enqueue (counting device-free attempts) or give up.
    Runs as owner (RLS-exempt); each row in its own transaction so one bad row can't abort the sweep.
    """
    from app.models.alert import Alert as AlertModel
    from app.models.config_change import ConfigChange
    from app.models.firmware_action import FirmwareAction

    settings = get_settings()
    grace = timedelta(minutes=settings.orphan_grace_minutes)
    max_attempts = settings.max_reenqueue_attempts
    now = datetime.now(UTC)
    cutoff = now - grace
    factory = ctx["session_factory"]
    redis = ctx["redis"]
    summary = {"re_enqueued": 0, "gave_up": 0, "skipped": 0}

    specs = [(ConfigChange, "apply_config_change"), (FirmwareAction, "run_firmware_action")]
    for model, job_name in specs:
        async with factory() as session:
            ids = (await session.execute(
                select(model.id).where(
                    model.status == "scheduled",
                    func.coalesce(model.scheduled_at, model.created_at) < cutoff,
                )
            )).scalars().all()
        for row_id in ids:
            try:
                async with factory() as session:
                    row = await session.get(model, row_id)
                    if row is None or row.status != "scheduled":
                        continue
                    got = (await session.execute(
                        text("SELECT pg_try_advisory_xact_lock(:k)"),
                        {"k": _advisory_key(row.device_id)},
                    )).scalar_one()
                    if not got:
                        summary["skipped"] += 1
                        await session.rollback()
                        continue
                    if decide_orphan(sweep_attempts=row.sweep_attempts, max_attempts=max_attempts) == "re-enqueue":
                        row.sweep_attempts += 1
                        await session.commit()
                        await redis.enqueue_job(job_name, str(row_id))
                        summary["re_enqueued"] += 1
                    else:
                        row.status = "failed"
                        row.result = {"error": f"orphaned: never applied after {row.sweep_attempts} re-enqueue attempts"}
                        session.add(AlertModel(tenant_id=row.tenant_id, device_id=row.device_id,
                                               type="action_orphaned",
                                               label=f"{job_name} {row_id} given up after {row.sweep_attempts} attempts"))
                        await session.commit()
                        summary["gave_up"] += 1
            except Exception:  # noqa: BLE001 — one bad row must not abort the sweep
                continue
    return summary


async def renew_device_certs(ctx: dict) -> dict:
    """Cron: rotate per-device forwarding certs nearing expiry (owner session, RLS-exempt)."""
    from app.connectors.opnsense.client import OpnsenseClient
    from app.core import crypto
    from app.services.cert_renewal import renew_expiring_device_certs

    settings = get_settings()
    factory = ctx["session_factory"]

    def client_for(device):
        return OpnsenseClient(device.base_url, crypto.decrypt(device.api_key_enc),
                              crypto.decrypt(device.api_secret_enc), verify_tls=device.verify_tls,
                              tls_fingerprint=device.tls_fingerprint)

    async with factory() as session:
        summary = await renew_expiring_device_certs(session, settings, client_for=client_for)
        await session.commit()
    return summary


async def enqueue_due_reports(ctx: dict) -> int:
    """Cron (hourly): enqueue a delivery job for each enabled schedule whose next_run_at is due."""
    from app.models.report_schedule import ReportSchedule

    factory = ctx["session_factory"]
    redis = ctx["redis"]
    now = datetime.now(UTC)
    async with factory() as session:
        ids = (await session.execute(
            select(ReportSchedule.id).where(
                ReportSchedule.enabled.is_(True),
                ReportSchedule.next_run_at.isnot(None),
                ReportSchedule.next_run_at <= now,
            )
        )).scalars().all()
    for sid in ids:
        await redis.enqueue_job("deliver_scheduled_report", str(sid))
    return len(ids)


async def deliver_scheduled_report(ctx: dict, schedule_id: str, manual: bool = False) -> str:
    """Job: build + store a report for a schedule, advance its cadence, enqueue the send.

    Runs as owner (RLS bypassed); the repositories scope every query by explicit tenant_id.
    """
    from app.models.report_schedule import ReportSchedule
    from app.models.tenant import Tenant
    from app.repositories.generated_report import GeneratedReportRepository
    from app.repositories.report_settings import ReportSettingsRepository
    from app.services.audit import AuditService
    from app.services.reporting.service import ReportService

    factory = ctx["session_factory"]
    redis = ctx["redis"]
    now = datetime.now(UTC)

    def _advance(s) -> None:
        s.last_run_at = now
        if not manual and s.frequency != ON_DEMAND:
            s.next_run_at = _next_run_at(s.frequency, s.weekday, s.hour, after=now)

    async with factory() as session:
        sched = await session.get(ReportSchedule, uuid.UUID(schedule_id))
        if sched is None:
            return "missing"
        if not manual and (not sched.enabled or sched.next_run_at is None or sched.next_run_at > now):
            return "skip"
        tenant = await session.get(Tenant, sched.tenant_id)
        if tenant is None:
            return "missing-tenant"
        if sched.device_id is not None:
            from app.models.device import Device
            if await session.get(Device, sched.device_id) is None:
                sched.enabled = False
                await AuditService(session).record(
                    actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.device_missing",
                    target_type="report_schedule", target_id=str(sched.id), ip=None, details={},
                )
                await session.commit()
                return "device-missing"
        try:
            frm, to = report_window(sched.frequency, run_at=now)
            settings = await ReportSettingsRepository(session, sched.tenant_id).get_or_default()
            pdf = await ReportService(session, sched.tenant_id).build_report(
                tenant_name=tenant.name, frm=frm, to=to, locale=settings.language,
                device_id=sched.device_id,
            )
            report = await GeneratedReportRepository(session, sched.tenant_id).create(
                kind="scheduled", period_from=frm, period_to=to, created_by=None, pdf=pdf,
                device_id=sched.device_id,
            )
        except Exception as exc:  # noqa: BLE001 — advance cadence so a broken build doesn't re-fire hourly
            await session.rollback()
            sched = await session.get(ReportSchedule, uuid.UUID(schedule_id))
            _advance(sched)
            await AuditService(session).record(
                actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.generate_failed",
                target_type="report_schedule", target_id=str(sched.id), ip=None,
                details={"error": str(exc)[:200]},
            )
            await session.commit()
            return "generate-failed"
        _advance(sched)
        await session.commit()
        await redis.enqueue_job("send_report_email_job", str(report.id), str(sched.id), 1)
        return "generated"


async def send_report_email_job(ctx: dict, report_id: str, schedule_id: str, attempt: int) -> str:
    """Job: email an already-stored report PDF to a schedule's recipients, with retry."""
    from app.models.generated_report import GeneratedReport
    from app.models.report_schedule import ReportSchedule
    from app.models.tenant import Tenant
    from app.repositories.report_settings import ReportSettingsRepository
    from app.services.audit import AuditService
    from app.services.smtp_settings import SmtpSettingsService

    factory = ctx["session_factory"]
    redis = ctx["redis"]

    async def _retry_or_give_up(session, sched, reason: str) -> str:
        if attempt < MAX_SEND_ATTEMPTS:
            await redis.enqueue_job("send_report_email_job", report_id, schedule_id, attempt + 1,
                                    _defer_by=RETRY_INTERVAL)
            return "retry"
        await AuditService(session).record(
            actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.failed",
            target_type="report_schedule", target_id=str(sched.id), ip=None,
            details={"error": reason, "attempts": attempt},
        )
        await session.commit()
        return "failed"

    async with factory() as session:
        sched = await session.get(ReportSchedule, uuid.UUID(schedule_id))
        report = await session.get(GeneratedReport, uuid.UUID(report_id))
        if sched is None or report is None:
            return "missing"
        if report.tenant_id != sched.tenant_id:
            await AuditService(session).record(
                actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.tenant_mismatch",
                target_type="report_schedule", target_id=str(sched.id), ip=None, details={},
            )
            await session.commit()
            return "tenant-mismatch"
        recipients = list(sched.recipients or [])
        if not recipients:
            await AuditService(session).record(
                actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.no_recipients",
                target_type="report_schedule", target_id=str(sched.id), ip=None, details={},
            )
            await session.commit()
            return "no-recipients"
        svc = SmtpSettingsService(session)
        smtp = await svc.get()
        if smtp is None or not smtp.enabled:
            return await _retry_or_give_up(session, sched, "smtp not configured")
        cfg = svc.to_send_config(smtp)
        settings = await ReportSettingsRepository(session, sched.tenant_id).get_or_default()
        if settings.from_email:
            cfg.from_email = settings.from_email
        tenant = await session.get(Tenant, sched.tenant_id)
        if tenant is None:
            return "missing-tenant"
        subject = (f"{settings.title} — {tenant.name} — "
                   f"{report.period_from:%Y-%m-%d}..{report.period_to:%Y-%m-%d}")
        try:
            await send_report_email(
                cfg, subject=subject, recipients=recipients,
                body_text="Your scheduled OPNGMS report is attached.",
                attachment=("opngms-report.pdf", report.pdf, "application/pdf"),
            )
        except EmailSendError as exc:
            return await _retry_or_give_up(session, sched, str(exc))
        await AuditService(session).record(
            actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.delivered",
            target_type="report_schedule", target_id=str(sched.id), ip=None,
            details={"recipients": len(recipients), "report_id": str(report.id)},
        )
        await session.commit()
        return "delivered"


async def detect_silent_tenants(ctx: dict) -> dict:
    """Cron: detect tenants gone silent (enabled forwarding, no recent logs), persist the alert
    state, and email the MSP superadmins ONCE per silent episode. Owner session (RLS-exempt)."""
    from app.models.user import User
    from app.services.email.smtp import EmailSendError, send_email
    from app.services.silent_alerts import detect_and_alert
    from app.services.smtp_settings import SmtpSettingsService

    settings = get_settings()
    factory = ctx["session_factory"]

    # Reconcile the alert state, gather the SMTP recipients/config, then COMMIT — and only email
    # AFTER the commit. Committing first means a post-email commit failure can't cause a duplicate
    # alert next run (the rows are already persisted -> dedup holds).
    async with factory() as session:
        summary = await detect_and_alert(session, settings)
        newly = summary["newly_silent"]
        cfg = recipients = None
        if newly:
            svc = SmtpSettingsService(session)
            smtp = await svc.get()
            if smtp is not None and smtp.enabled:
                recipients = [row[0] for row in (await session.execute(
                    select(User.email).where(User.is_superadmin.is_(True), User.status == "active")
                )).all()]
                cfg = svc.to_send_config(smtp) if recipients else None
        await session.commit()

    emailed = False
    if cfg and recipients:
        # tenant_name is operator-controlled — strip CR/LF before it reaches the Subject (defence in
        # depth; smtp._strip also sanitises).
        names = [name.replace("\r", " ").replace("\n", " ") for _id, name in newly]
        body = (
            "These OPNGMS tenant(s) have enabled log forwarding but stopped shipping logs "
            f"(silent > {settings.silent_alert_after_hours}h):\n\n  "
            + "\n  ".join(names)
            + "\n\nOpen the Log fleet dashboard to investigate."
        )
        try:
            await send_email(cfg, subject=f"OPNGMS: {len(names)} tenant(s) silent — {', '.join(names)}",
                             recipients=recipients, body_text=body)
            emailed = True
        except EmailSendError:
            emailed = False
    return {**summary, "emailed": emailed}


async def cleanup_expired_sessions(ctx: dict) -> str:
    """Cron: delete expired/idle sessions. Returns a short status string."""
    factory = ctx["session_factory"]
    async with factory() as session:
        from app.services.auth import AuthService  # local import avoids a cycle at module load

        n = await AuthService(session).purge_expired(datetime.now(UTC))
        await session.commit()
    return f"purged {n} expired sessions"


async def on_startup(ctx: dict) -> None:
    engine = create_async_engine(_owner_url(), pool_pre_ping=True)
    ctx["engine"] = engine
    ctx["session_factory"] = async_sessionmaker(engine, expire_on_commit=False)


async def on_shutdown(ctx: dict) -> None:
    await ctx["engine"].dispose()


_settings = get_settings()
# Event-ingest cadence: every N minutes (clamped to 1..30 so the range step is valid).
_ingest_step = min(30, max(1, _settings.ingest_every_minutes))

MAX_SEND_ATTEMPTS = 12          # 1 send + retries every RETRY_INTERVAL, ~2h total
RETRY_INTERVAL = 600            # seconds between send retries


class WorkerSettings:
    functions = [poll_device, ingest_device_events, backup_device_config, apply_config_change, run_firmware_action, deliver_scheduled_report, send_report_email_job]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),  # metrics, every minute at second 0
        cron(enqueue_event_ingests, minute=set(range(0, 60, _ingest_step))),  # events, every N minutes
        cron(enqueue_config_backups, hour={_settings.config_backup_hour}, minute={0}),  # config, daily
        cron(enqueue_due_reports, minute={0}),  # hourly: fire due report schedules
        cron(cleanup_expired_sessions, minute={_settings.session_cleanup_minute}),  # hourly: reap sessions
        cron(sweep_orphaned_actions, minute=set(range(0, 60, min(30, max(1, _settings.sweep_every_minutes))))),
        cron(renew_device_certs, hour={_settings.cert_renewal_hour}, minute={0}),  # daily: renew expiring certs
        cron(detect_silent_tenants, minute={_settings.silent_alert_cron_minute}),  # hourly: silent-tenant alerts
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
