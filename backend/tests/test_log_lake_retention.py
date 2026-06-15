"""SP-2 log-lake retention: pure index parsing + the per-tenant delete decision.

The pure-logic tests (Task 3) need no infra. The OpenSearch purge (Task 4) is mocked with respx below.
"""
import uuid
from datetime import date

import httpx
import respx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.tenant_retention import TenantRetentionRepository
from app.services.log_lake_retention import indices_to_delete, parse_index, purge_log_lake

_UUID = "3f6a7b8c-1d2e-4f50-9a1b-2c3d4e5f6071"
_OS_URL = "http://opensearch.test:9200"


def test_parse_index_tenant_tagged():
    assert parse_index(f"opngms-logs-{_UUID}-2026.06.10") == (_UUID, date(2026, 6, 10))


def test_parse_index_legacy_date_only():
    # No tenant segment → tenant_id None (legacy shared index).
    assert parse_index("opngms-logs-2026.06.10") == (None, date(2026, 6, 10))


def test_parse_index_non_matching():
    assert parse_index("opngms-logs-weird") is None
    assert parse_index("other-index") is None
    # A 36-char-shaped segment that is NOT a valid UUID is rejected.
    assert parse_index("opngms-logs-zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz-2026.06.10") is None
    # An out-of-range date is rejected (no silent ValueError leak).
    assert parse_index("opngms-logs-2026.13.40") is None


def test_indices_to_delete_per_tenant_and_legacy():
    today = date(2026, 6, 20)
    aaaa = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
    bbbb = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb"
    names = [
        f"opngms-logs-{aaaa}-2026.06.01",  # tenant aaaa, 19d old
        f"opngms-logs-{aaaa}-2026.06.19",  # tenant aaaa, 1d old (kept)
        f"opngms-logs-{bbbb}-2026.06.01",  # tenant bbbb, 19d old
        "opngms-logs-2026.05.01",          # legacy, 50d old
        "unrelated-index",                 # ignored (not ours)
    ]
    overrides = {aaaa: {"log_lake": 7}}  # aaaa keeps 7d; bbbb + legacy use the global 30
    to_del = indices_to_delete(names, today, global_default=30, overrides_by_tenant=overrides)
    # aaaa@7d: 19d>7 -> delete the 06.01; 1d kept. bbbb@30d: 19d<30 kept. legacy@30d: 50d>30 -> delete.
    assert set(to_del) == {f"opngms-logs-{aaaa}-2026.06.01", "opngms-logs-2026.05.01"}


def test_indices_to_delete_boundary_is_strict_greater_than():
    # Exactly `days` old is kept; older than `days` is deleted (strict >, matching the SP-1 cutoff).
    today = date(2026, 6, 20)
    names = ["opngms-logs-2026.05.21", "opngms-logs-2026.05.20"]  # 30d and 31d old
    to_del = indices_to_delete(names, today, global_default=30, overrides_by_tenant={})
    assert to_del == ["opngms-logs-2026.05.20"]


# ── Task 4: the OpenSearch purge (HTTP mocked with respx) ──────────────────────────────────────────


def _sf(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


async def _seed_tenant(db_engine, slug):
    tid = uuid.uuid4()
    async with _sf(db_engine)() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,:n,:s,'active')"),
                        {"i": tid, "n": slug, "s": slug})
        await s.commit()
    return tid


async def _set_override(db_engine, tid, patch):
    async with _sf(db_engine)() as s:
        await set_tenant_context(s, tid)
        await TenantRetentionRepository(s, tid).upsert(patch)
        await s.commit()


async def test_purge_no_op_when_url_unset(db_engine):
    # No OpenSearch URL → the log lake isn't deployed → no-op, no HTTP (respx not even mounted here).
    async with _sf(db_engine)() as s:
        assert await purge_log_lake(s, date(2026, 6, 20), opensearch_url="") == "skipped"
        assert await purge_log_lake(s, date(2026, 6, 20), opensearch_url=None) == "skipped"


@respx.mock
async def test_purge_deletes_over_age_indices_per_tenant(db_engine):
    """End to end (DB overrides + mocked OpenSearch): the over-age per-tenant index is deleted at the
    tenant's override, a generous override keeps an old index, the legacy index uses the global default,
    and fresh indices are kept."""
    aaaa = await _seed_tenant(db_engine, "ll-aaaa")  # 7-day override → its old index is purged
    bbbb = await _seed_tenant(db_engine, "ll-bbbb")  # 365-day override → its old index survives
    await _set_override(db_engine, aaaa, {"log_lake": 7})
    await _set_override(db_engine, bbbb, {"log_lake": 365})

    today = date(2026, 6, 20)
    indices = [
        f"opngms-logs-{aaaa}-2026.06.01",  # aaaa, 19d > 7  -> delete
        f"opngms-logs-{aaaa}-2026.06.19",  # aaaa, 1d       -> keep
        f"opngms-logs-{bbbb}-2026.01.01",  # bbbb, 170d < 365 (override) -> keep
        "opngms-logs-2026.05.01",          # legacy, 50d > 30 (global)   -> delete
        "opngms-logs-2026.06.19",          # legacy, 1d                  -> keep
    ]
    cat = respx.get(f"{_OS_URL}/_cat/indices/opngms-logs-*").mock(
        return_value=httpx.Response(200, json=[{"index": n} for n in indices])
    )
    deletes = respx.delete(url__regex=rf"{_OS_URL}/opngms-logs-.+").mock(
        return_value=httpx.Response(200, json={"acknowledged": True})
    )

    async with _sf(db_engine)() as s:
        deleted = await purge_log_lake(s, today, opensearch_url=_OS_URL)

    assert cat.called
    assert deleted == 2
    deleted_paths = {call.request.url.path.lstrip("/") for call in deletes.calls}
    assert deleted_paths == {f"opngms-logs-{aaaa}-2026.06.01", "opngms-logs-2026.05.01"}


@respx.mock
async def test_purge_unreachable_is_best_effort(db_engine):
    # A connection error on the listing returns "unreachable" (best-effort), never raises.
    respx.get(f"{_OS_URL}/_cat/indices/opngms-logs-*").mock(
        side_effect=httpx.ConnectError("down")
    )
    async with _sf(db_engine)() as s:
        assert await purge_log_lake(s, date(2026, 6, 20), opensearch_url=_OS_URL) == "unreachable"
