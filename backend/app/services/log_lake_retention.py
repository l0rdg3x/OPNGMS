"""Per-tenant retention for the OpenSearch log lake (SP-2).

The log lake is the 4th retention store. syslog-ng writes per-tenant daily indices
``opngms-logs-<tenant_id>-<YYYY>.<MM>.<DD>`` (plus any pre-SP-2 ``opngms-logs-<YYYY>.<MM>.<DD>`` legacy
date-only indices). A daily worker job lists the indices and deletes each one whose date is older than
its tenant's effective retention (per-tenant override over the global default, via the SP-1 resolver);
legacy date-only indices use the global default.

Every OpenSearch touch is best-effort and no-ops gracefully when the log lake isn't deployed/reachable —
the lake is optional (only the ``logs``/``full`` compose overlay runs it). The worker runs as the DB owner
(RLS-exempt) so it can read every tenant's overrides in one query.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant_retention import TenantRetention
from app.services.retention import effective_retention_days
from app.services.runtime_settings import get_runtime_config

logger = logging.getLogger(__name__)

# opngms-logs-<tenant_id?>-YYYY.MM.DD. The tenant segment is optional (legacy shared indices have none);
# a present segment is a 36-char UUID shape, validated as a real UUID below.
_RE = re.compile(r"^opngms-logs-(?:(?P<tid>[0-9a-fA-F-]{36})-)?(?P<y>\d{4})\.(?P<m>\d{2})\.(?P<d>\d{2})$")


def parse_index(name: str) -> tuple[str | None, date] | None:
    """``(tenant_id|None, date)`` for an ``opngms-logs`` index name, else ``None``.

    ``tenant_id is None`` flags a legacy date-only index. A non-UUID tenant segment or an out-of-range
    date (e.g. ``2026.13.40``) yields ``None`` (not ours / malformed → never matched for deletion).
    """
    m = _RE.match(name)
    if not m:
        return None
    tid = m.group("tid")
    if tid is not None:
        try:
            uuid.UUID(tid)
        except ValueError:
            return None
    try:
        idx_date = date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except ValueError:
        return None
    return tid, idx_date


def indices_to_delete(
    index_names,
    today: date,
    *,
    global_default: int,
    overrides_by_tenant: dict[str, dict],
) -> list[str]:
    """The indices whose date is older than their tenant's effective log_lake retention.

    Legacy date-only indices (no tenant segment) use ``global_default``. Non-matching names are ignored.
    A tenant's override is looked up by id in ``overrides_by_tenant`` and resolved through the SP-1
    ``effective_retention_days`` (an invalid/out-of-range override falls back to the global default).
    """
    out: list[str] = []
    for name in index_names:
        parsed = parse_index(name)
        if parsed is None:
            continue  # not ours / malformed
        tid, idx_date = parsed
        override = overrides_by_tenant.get(tid) if tid else None
        days = effective_retention_days(
            "log_lake", global_default=global_default, tenant_override=override
        )
        if (today - idx_date).days > days:
            out.append(name)
    return out


async def purge_log_lake(
    session: AsyncSession, today: date, *, opensearch_url: str | None
) -> int | str:
    """Delete per-tenant log-lake indices past each tenant's effective retention. Returns the number
    of indices deleted, or a short status string for the degraded paths.

    Owner session (RLS-exempt — sees every ``tenant_retention`` row). Best-effort and graceful:
    - ``opensearch_url`` falsy → ``"skipped"`` (the log lake isn't deployed); no HTTP.
    - OpenSearch unreachable on the listing → ``"unreachable"`` (logged); no raise.
    Each DELETE is independent — one failing index (logged) doesn't abort the sweep.
    """
    if not opensearch_url:
        return "skipped"

    cfg = await get_runtime_config(session)
    global_default = int(cfg["log_lake_retention_days"])
    # All tenants' overrides in one owner-side read (no RLS) → {tenant_id: overrides}.
    overrides_by_tenant = {
        str(tid): dict(ov or {})
        for tid, ov in (
            await session.execute(
                select(TenantRetention.tenant_id, TenantRetention.overrides)
            )
        ).all()
    }

    # List the lake's indices (same httpx client style as services/log_fleet.py). Best-effort.
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{opensearch_url}/_cat/indices/opngms-logs-*",
                params={"format": "json", "h": "index"},
            )
            resp.raise_for_status()
            rows = resp.json()
    except (httpx.HTTPError, ValueError):
        # Expected in a core-only deployment (no log lake): OPENSEARCH_URL has a non-empty default but
        # nothing listens. Log at INFO so this isn't daily WARNING noise; an operator running the lake
        # sees its health on the Log fleet dashboard. Set OPENSEARCH_URL= (empty) to skip entirely.
        logger.info("log-lake retention: OpenSearch unreachable; skipping this run")
        return "unreachable"

    names = [r["index"] for r in rows if isinstance(r, dict) and r.get("index")]
    victims = indices_to_delete(
        names, today, global_default=global_default, overrides_by_tenant=overrides_by_tenant
    )

    deleted = 0
    async with httpx.AsyncClient(timeout=15.0) as client:
        for name in victims:
            try:
                resp = await client.delete(f"{opensearch_url}/{name}")
                resp.raise_for_status()
                deleted += 1
            except httpx.HTTPError:
                # One bad delete must not abort the sweep; log and continue.
                logger.warning("log-lake retention: failed to delete index %s", name, exc_info=True)
    return deleted
