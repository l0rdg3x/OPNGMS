"""Tests for the generate_tenant_report ARQ job and the enqueue_scheduled_reports cron."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.generated_report import GeneratedReport
from app.worker import _prior_week, enqueue_scheduled_reports, generate_tenant_report


# ---------------------------------------------------------------------------
# Fake redis that records enqueue_job calls
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self):
        self.calls: list[tuple] = []

    async def enqueue_job(self, name: str, *args, **kwargs):
        self.calls.append((name, *args))


# ---------------------------------------------------------------------------
# Test: generate_tenant_report stores a PDF row
# ---------------------------------------------------------------------------

async def test_generate_tenant_report_stores_pdf(db_engine):
    """generate_tenant_report inserts a generated_reports row with a real PDF."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Seed tenant + device + two IDS events (owner session, bypasses RLS)
    tid = uuid.uuid4()
    did = uuid.uuid4()
    frm_dt = datetime(2026, 5, 1, tzinfo=timezone.utc)
    to_dt = datetime(2026, 6, 1, tzinfo=timezone.utc)

    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'Acme', 'acme', 'active')"),
            {"id": tid},
        )
        await s.execute(
            text(
                "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
                "VALUES (:id, :t, 'fw1', 'https://fw', ''::bytea, ''::bytea, true, 'reachable', '{}')"
            ),
            {"id": did, "t": tid},
        )
        # Two IDS events to populate the report
        for i in range(2):
            await s.execute(
                text(
                    "INSERT INTO events (time, device_id, source, event_key, tenant_id, name, src_ip, dst_ip) "
                    "VALUES (:t, :d, 'ids', :k, :tid, 'ET SCAN', '10.0.0.1', '8.8.8.8')"
                ),
                {"t": datetime(2026, 5, 15, 12, i, tzinfo=timezone.utc), "d": did, "k": f"ev{i}", "tid": tid},
            )
        await s.commit()

    ctx = {"session_factory": factory}
    result = await generate_tenant_report(ctx, str(tid), frm_dt.isoformat(), to_dt.isoformat(), "scheduled")

    assert result == "stored"

    # Verify the row was persisted
    async with factory() as s:
        rows = (
            await s.execute(
                select(GeneratedReport).where(GeneratedReport.tenant_id == tid)
            )
        ).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.pdf[:5] == b"%PDF-"
    assert row.kind == "scheduled"
    assert row.created_by is None
    assert row.size > 0
    assert row.size == len(row.pdf)
    assert row.period_from.replace(tzinfo=timezone.utc) == frm_dt
    assert row.period_to.replace(tzinfo=timezone.utc) == to_dt


async def test_generate_tenant_report_missing_tenant(db_engine):
    """generate_tenant_report returns 'missing-tenant' when the tenant does not exist."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    ctx = {"session_factory": factory}
    non_existent = str(uuid.uuid4())
    frm = datetime(2026, 5, 1, tzinfo=timezone.utc).isoformat()
    to = datetime(2026, 6, 1, tzinfo=timezone.utc).isoformat()
    result = await generate_tenant_report(ctx, non_existent, frm, to, "scheduled")
    assert result == "missing-tenant"


# ---------------------------------------------------------------------------
# Test: enqueue_scheduled_reports enumerates active tenants
# ---------------------------------------------------------------------------

async def test_enqueue_scheduled_reports_enumerates_active_tenants(db_engine):
    """enqueue_scheduled_reports enqueues exactly one job per active tenant."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    # Seed 2 active tenants + 1 inactive
    tid_a, tid_b, tid_inactive = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text(
                "INSERT INTO tenants (id, name, slug, status) VALUES "
                "(:a, 'Alpha', 'alpha', 'active'), "
                "(:b, 'Beta', 'beta', 'active'), "
                "(:c, 'Gamma', 'gamma', 'inactive')"
            ),
            {"a": tid_a, "b": tid_b, "c": tid_inactive},
        )
        await s.commit()

    fake_redis = FakeRedis()
    ctx = {"session_factory": factory, "redis": fake_redis}

    count = await enqueue_scheduled_reports(ctx)

    assert count == 2
    assert len(fake_redis.calls) == 2

    # All enqueued jobs must be named "generate_tenant_report"
    job_names = {call[0] for call in fake_redis.calls}
    assert job_names == {"generate_tenant_report"}

    # All enqueued jobs must carry kind="scheduled"
    for call in fake_redis.calls:
        # call is (name, tenant_id_str, frm_iso, to_iso, kind)
        assert call[4] == "scheduled"

    # Verify the prior-week range:
    # period_to must be Monday 00:00 of the current week (UTC).
    # period_from must be Monday 00:00 of the previous week (UTC).
    # The span must be exactly 7 days.
    now = datetime.now(timezone.utc)
    expected_frm, expected_to = _prior_week(now)

    for call in fake_redis.calls:
        # Allow for the (very unlikely) edge case where the week boundary was crossed between
        # _prior_week above and the cron call — compare parsed values independently.
        frm_parsed = datetime.fromisoformat(call[2])
        to_parsed = datetime.fromisoformat(call[3])
        # period_to is always a Monday at 00:00
        assert to_parsed.weekday() == 0, "period_to must be a Monday"
        assert to_parsed.hour == 0 and to_parsed.minute == 0 and to_parsed.second == 0
        # period_from is also a Monday at 00:00
        assert frm_parsed.weekday() == 0, "period_from must be a Monday"
        assert frm_parsed.hour == 0 and frm_parsed.minute == 0 and frm_parsed.second == 0
        # The range spans exactly 7 days
        assert to_parsed - frm_parsed == timedelta(days=7)
        # The range matches _prior_week(now)
        assert to_parsed == expected_to

    # The inactive tenant must NOT be enqueued
    enqueued_ids = {call[1] for call in fake_redis.calls}
    assert str(tid_inactive) not in enqueued_ids


async def test_enqueue_scheduled_reports_no_active_tenants(db_engine):
    """enqueue_scheduled_reports returns 0 and enqueues nothing when no active tenants exist."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    fake_redis = FakeRedis()
    ctx = {"session_factory": factory, "redis": fake_redis}

    count = await enqueue_scheduled_reports(ctx)

    assert count == 0
    assert len(fake_redis.calls) == 0
