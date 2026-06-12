import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.worker as worker
from app.core.db import set_tenant_context
from app.models.generated_report import GeneratedReport
from app.models.report_schedule import ReportSchedule
from app.services.smtp_settings import SmtpSettingsService


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))


async def _seed_schedule(factory, *, enabled=True, next_run_at, frequency="weekly", weekday=0):
    tid, did = uuid.uuid4(), uuid.uuid4()
    slug = f"acme-{tid.hex[:8]}"
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'Acme',:slug,'active')"),
                        {"i": tid, "slug": slug})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        s.add(ReportSchedule(tenant_id=tid, device_id=None, enabled=enabled, frequency=frequency,
                             weekday=weekday, hour=4, recipients=["a@x.io"], next_run_at=next_run_at))
        await s.commit()
    return tid, did


async def test_enqueue_due_reports_picks_due_only(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    await _seed_schedule(factory, next_run_at=datetime(2999, 1, 1, tzinfo=UTC))
    redis = FakeRedis()
    n = await worker.enqueue_due_reports({"session_factory": factory, "redis": redis})
    assert n == 1
    assert redis.calls[0][0] == "deliver_scheduled_report"


async def test_deliver_builds_stores_advances_and_enqueues_send(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, _ = await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    async with factory() as s:
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()
    redis = FakeRedis()
    res = await worker.deliver_scheduled_report({"session_factory": factory, "redis": redis}, str(sid))
    assert res == "generated"
    async with factory() as s:
        rep = (await s.execute(select(GeneratedReport))).scalar_one()
        assert rep.kind == "scheduled" and rep.pdf[:5] == b"%PDF-"
        sched = await s.get(ReportSchedule, sid)
        assert sched.last_run_at is not None
        assert sched.next_run_at > datetime(2020, 1, 1, tzinfo=UTC)
    assert redis.calls[0][0] == "send_report_email_job"
    assert redis.calls[0][1][0] == str(rep.id)


async def test_send_job_delivers_on_success(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, _ = await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    async with factory() as s:
        await SmtpSettingsService(s).upsert(enabled=True, host="h", port=587, security="starttls",
            username="u", from_email="noc@x.io", from_name="N", password="p", clear_password=False)
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()
        await s.commit()
    redis = FakeRedis()
    await worker.deliver_scheduled_report({"session_factory": factory, "redis": redis}, str(sid))
    report_id = redis.calls[0][1][0]

    sent = {}
    async def fake_send(cfg, **kw):
        sent["recipients"] = kw["recipients"]
        sent["from"] = cfg.from_email
    monkeypatch.setattr(worker, "send_report_email", fake_send)
    res = await worker.send_report_email_job({"session_factory": factory, "redis": redis}, report_id, str(sid), 1)
    assert res == "delivered"
    assert sent["recipients"] == ["a@x.io"]


async def test_send_job_retries_then_gives_up(db_engine, monkeypatch):
    from app.services.email.smtp import EmailSendError
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    async with factory() as s:
        await SmtpSettingsService(s).upsert(enabled=True, host="h", port=587, security="starttls",
            username="u", from_email="noc@x.io", from_name="N", password="p", clear_password=False)
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()
        await s.commit()
    redis = FakeRedis()
    await worker.deliver_scheduled_report({"session_factory": factory, "redis": redis}, str(sid))
    report_id = redis.calls[0][1][0]
    redis.calls.clear()

    async def boom(cfg, **kw):
        raise EmailSendError("nope")
    monkeypatch.setattr(worker, "send_report_email", boom)
    r1 = await worker.send_report_email_job({"session_factory": factory, "redis": redis}, report_id, str(sid), 1)
    assert r1 == "retry"
    assert redis.calls[0][0] == "send_report_email_job"
    assert redis.calls[0][1][2] == 2
    assert redis.calls[0][2].get("_defer_by") == worker.RETRY_INTERVAL
    r_last = await worker.send_report_email_job({"session_factory": factory, "redis": redis}, report_id, str(sid), worker.MAX_SEND_ATTEMPTS)
    assert r_last == "failed"
