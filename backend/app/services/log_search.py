"""Tenant-scoped OpenSearch log search: query builder + HTTP client (the only OpenSearch client)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx

MAX_SIZE = 200
PIT_KEEPALIVE = "2m"  # Point-In-Time TTL; refreshed on each search, expires the cursor when idle.


def build_search_body(*, tenant_id: uuid.UUID, frm: datetime, to: datetime, query: str,
                      device_id: uuid.UUID | None, size: int, pit_id: str,
                      search_after: list | None = None) -> dict:
    """Build the OpenSearch PIT `_search` body. The tenant_id + time-range filters are ALWAYS present
    (the tenant filter is injected from the RBAC-verified path — a query_string lands in `must` and can
    never widen past it). Paging is via PIT + search_after over a stable [@timestamp, _shard_doc] sort,
    so it is unbounded and consistent across the second-granularity timestamp ties our logs produce."""
    filters: list[dict] = [
        {"term": {"tenant_id": str(tenant_id)}},
        {"range": {"@timestamp": {"gte": frm.isoformat(), "lte": to.isoformat()}}},
    ]
    if device_id is not None:
        filters.append({"term": {"device_id": str(device_id)}})
    bool_q: dict = {"filter": filters}
    if query:
        bool_q["must"] = [{
            "query_string": {
                "query": query,
                "default_field": "message",
                "allow_leading_wildcard": False,
                "analyze_wildcard": False,
                "lenient": True,
            }
        }]
    body: dict = {
        "query": {"bool": bool_q},
        "pit": {"id": pit_id, "keep_alive": PIT_KEEPALIVE},
        "sort": [{"@timestamp": "desc"}, {"_shard_doc": "asc"}],
        "size": min(size, MAX_SIZE),
        "track_total_hits": True,
    }
    if search_after is not None:
        body["search_after"] = search_after
    return body


@dataclass
class LogHit:
    id: str
    timestamp: str
    device_id: str
    host: str
    program: str
    message: str
    source: dict


@dataclass
class SearchResult:
    total: int
    hits: list[LogHit]
    next_cursor: dict | None = None


class LogSearchError(Exception):
    """OpenSearch transport/query failure (mapped to 502 by the API)."""


async def open_pit(settings) -> str:
    """Open an OpenSearch Point-In-Time over the log indices; returns its id."""
    url = f"{settings.opensearch_url}/opngms-logs-*/_pit"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"keep_alive": PIT_KEEPALIVE, "ignore_unavailable": "true"})
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LogSearchError(str(exc)[:200]) from exc
    pit_id = data.get("pit_id") or data.get("id")
    if not pit_id:
        raise LogSearchError("OpenSearch did not return a pit_id")
    return str(pit_id)


async def search_logs(settings, *, tenant_id, frm, to, query, device_id, size, cursor=None) -> SearchResult:
    """Tenant-scoped PIT + search_after search. With no cursor, opens a PIT and returns page 1; with a
    cursor ({pit_id, after}) it continues. Returns hits + a next_cursor (None on the last page)."""
    eff_size = min(size, MAX_SIZE)
    if cursor is None:
        pit_id = await open_pit(settings)
        search_after = None
    else:
        pit_id = cursor["pit_id"]
        search_after = cursor["after"]
    body = build_search_body(tenant_id=tenant_id, frm=frm, to=to, query=query, device_id=device_id,
                             size=eff_size, pit_id=pit_id, search_after=search_after)
    url = f"{settings.opensearch_url}/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LogSearchError(str(exc)[:200]) from exc
    total = (data.get("hits", {}).get("total", {}) or {}).get("value", 0)
    hits: list[LogHit] = []
    last_sort = None
    for h in data.get("hits", {}).get("hits", []):
        src = h.get("_source", {}) or {}
        hits.append(LogHit(
            id=str(h.get("_id", "")),
            timestamp=str(src.get("@timestamp", "")),
            device_id=str(src.get("device_id", "")),
            host=str(src.get("host", "")),
            program=str(src.get("program", "")),
            message=str(src.get("message", "")),
            source=src,
        ))
        last_sort = h.get("sort")
    next_cursor = None
    if len(hits) == eff_size and last_sort is not None:
        next_cursor = {"pit_id": str(data.get("pit_id") or pit_id), "after": last_sort}
    return SearchResult(total=int(total), hits=hits, next_cursor=next_cursor)


async def latest_log_at(settings, *, tenant_id: uuid.UUID, device_id: uuid.UUID) -> datetime | None:
    """Best-effort @timestamp of the most recent log for this device, or None if there are no logs
    or OpenSearch is unreachable. Keeps the mandatory tenant filter (same guarantee as search_logs)."""
    body = {
        "query": {"bool": {"filter": [
            {"term": {"tenant_id": str(tenant_id)}},
            {"term": {"device_id": str(device_id)}},
        ]}},
        "sort": [{"@timestamp": "desc"}],
        "size": 1,
        "_source": ["@timestamp"],
    }
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        if not hits:
            return None
        ts = (hits[0].get("_source", {}) or {}).get("@timestamp")
        if not ts:
            return None
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (httpx.HTTPError, ValueError, KeyError, AttributeError, TypeError):
        # Best-effort liveness: a malformed OpenSearch response (e.g. a non-dict hit element)
        # must never raise into the status request — degrade to "unknown".
        return None
