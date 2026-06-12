"""Tenant-scoped OpenSearch log search: query builder + HTTP client (the only OpenSearch client)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import httpx

MAX_SIZE = 200


def build_search_body(*, tenant_id: uuid.UUID, frm: datetime, to: datetime, query: str,
                      device_id: uuid.UUID | None, page: int, size: int) -> dict:
    """Build the OpenSearch _search body. The tenant_id + time-range filters are ALWAYS present;
    a non-empty `query` becomes a guarded query_string in `must` (ANDed with the filter — it can
    never widen past the tenant scope)."""
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
    return {
        "query": {"bool": bool_q},
        "sort": [{"@timestamp": "desc"}],
        "from": max(0, page) * min(size, MAX_SIZE),
        "size": min(size, MAX_SIZE),
        "track_total_hits": True,
    }


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


class LogSearchError(Exception):
    """OpenSearch transport/query failure (mapped to 502 by the API)."""


async def search_logs(settings, *, tenant_id, frm, to, query, device_id, page, size) -> SearchResult:
    """POST the search to OpenSearch (internal URL, plain HTTP) and map the response."""
    body = build_search_body(tenant_id=tenant_id, frm=frm, to=to, query=query,
                             device_id=device_id, page=page, size=size)
    url = f"{settings.opensearch_url}/opngms-logs-*/_search"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, params={"ignore_unavailable": "true"}, json=body)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise LogSearchError(str(exc)[:200]) from exc
    total = (data.get("hits", {}).get("total", {}) or {}).get("value", 0)
    hits: list[LogHit] = []
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
    return SearchResult(total=int(total), hits=hits)
