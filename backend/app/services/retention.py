"""Per-tenant retention: the resolver (global default < per-tenant override) + the tenant-aware purge.

The purge runs in the worker as the DB owner (RLS-exempt — the only role that can drop TimescaleDB
retention policies and that sees every tenant). It is NEVER called on a user-facing path.

Migration 0039 removes the native TimescaleDB retention policies on events/metrics; the per-tenant
``purge_events`` / ``purge_metrics`` sweeps below (run from the worker cron) take over enforcement.
"""
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RETENTION_STORES = ("perimeter", "events", "metrics", "log_lake")
_MIN, _MAX = 1, 3650


def effective_retention_days(store: str, *, global_default: int, tenant_override: dict | None) -> int:
    v = (tenant_override or {}).get(store)
    if isinstance(v, bool):  # bool is an int subclass — reject before the int check
        return global_default
    return v if isinstance(v, int) and _MIN <= v <= _MAX else global_default


async def _purge_table(session: AsyncSession, *, table: str, time_col: str, store: str,
                       now: datetime, global_default: int) -> int:
    """One statement: per-tenant cutoff from (tenants LEFT JOIN tenant_retention), clamped to [1,3650].

    `table`, `time_col` and `store` are internal constants (only this module's wrappers pass them) — never
    request-derived — so the f-string interpolation is safe by construction. The override is read defensively:
    only an all-digits string is used (a hand-edited / non-numeric JSONB value falls back to the global
    default instead of raising a cast error that would abort the whole purge).
    """
    stmt = text(f"""
        DELETE FROM {table} d
        USING (
            SELECT t.id AS tenant_id,
                   CAST(:now AS timestamptz) - make_interval(days => CASE
                       WHEN tr.overrides->>'{store}' ~ '^[0-9]+$'
                       THEN LEAST(:mx, GREATEST(:mn, (tr.overrides->>'{store}')::int))
                       ELSE :gd
                   END) AS cutoff
            FROM tenants t
            LEFT JOIN tenant_retention tr ON tr.tenant_id = t.id
        ) c
        WHERE d.tenant_id = c.tenant_id AND d.{time_col} < c.cutoff
    """)
    res = await session.execute(stmt, {"now": now, "gd": global_default, "mn": _MIN, "mx": _MAX})
    return res.rowcount or 0


async def purge_events(session: AsyncSession, now: datetime, *, global_default: int) -> int:
    """Per-tenant retention sweep on the events hypertable (time < each tenant's effective cutoff).

    Replaces the native TimescaleDB retention policy removed in migration 0039."""
    return await _purge_table(session, table="events", time_col="time",
                              store="events", now=now, global_default=global_default)


async def purge_metrics(session: AsyncSession, now: datetime, *, global_default: int) -> int:
    """Per-tenant retention sweep on the metrics hypertable (time < each tenant's effective cutoff).

    Replaces the native TimescaleDB retention policy removed in migration 0039."""
    return await _purge_table(session, table="metrics", time_col="time",
                              store="metrics", now=now, global_default=global_default)
