"""Per-tenant retention: the resolver (global default < per-tenant override) + the tenant-aware purge.

The purge runs in the worker as the DB owner (RLS-exempt — the only role that can drop TimescaleDB
retention policies and that sees every tenant). It is NEVER called on a user-facing path.

Disk caveat: until PR2 removes the native TimescaleDB retention policies on events/metrics, only the
perimeter rollup is swept by this helper; events/metrics keep their global native policy until then.
"""
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

RETENTION_STORES = ("perimeter", "events", "metrics")  # SP-2 will add "log_lake"
_MIN, _MAX = 1, 3650


def effective_retention_days(store: str, *, global_default: int, tenant_override: dict | None) -> int:
    v = (tenant_override or {}).get(store)
    if isinstance(v, bool):  # bool is an int subclass — reject before the int check
        return global_default
    return v if isinstance(v, int) and _MIN <= v <= _MAX else global_default


async def _purge_table(session: AsyncSession, *, table: str, time_col: str, store: str,
                       now: datetime, global_default: int) -> int:
    """One statement: per-tenant cutoff from (tenants LEFT JOIN tenant_retention), clamped to [1,3650]."""
    stmt = text(f"""
        DELETE FROM {table} d
        USING (
            SELECT t.id AS tenant_id,
                   CAST(:now AS timestamptz) - make_interval(days => LEAST(:mx, GREATEST(:mn,
                       COALESCE(NULLIF(tr.overrides->>'{store}', '')::int, :gd)))) AS cutoff
            FROM tenants t
            LEFT JOIN tenant_retention tr ON tr.tenant_id = t.id
        ) c
        WHERE d.tenant_id = c.tenant_id AND d.{time_col} < c.cutoff
    """)
    res = await session.execute(stmt, {"now": now, "gd": global_default, "mn": _MIN, "mx": _MAX})
    return res.rowcount or 0
