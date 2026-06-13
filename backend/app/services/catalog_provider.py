"""Fetch + cache + verify versioned OPNsense catalogs published as GitHub Release assets.

A Business device is served the Community catalog of its base version (resolve_target maps it via
business-base.json). The catalog file's SHA-256 is verified against the manifest before it is cached
or used. Offline, a previously-cached catalog is still served; a cold offline start returns None.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.catalog_cache import CatalogCache

_NUM = re.compile(r"\d+")


def _parse_version(v: str) -> tuple[int, ...]:
    """'26.1.8' -> (26, 1, 8). Tolerant of suffixes ('26.1.8_4' -> (26, 1, 8))."""
    parts: list[int] = []
    for p in v.split("."):
        m = _NUM.match(p)
        parts.append(int(m.group()) if m else 0)
    return tuple(parts)


def resolve_version(versions: list[str], version: str) -> str | None:
    """Exact match else the highest published version <= `version`. None if none <=."""
    if version in versions:
        return version
    target = _parse_version(version)
    below = [v for v in versions if _parse_version(v) <= target]
    return max(below, key=_parse_version) if below else None


def _community_versions(manifest: dict) -> list[str]:
    return [k.split("/", 1)[1] for k in manifest.get("catalogs", {}) if k.startswith("community/")]


def resolve_target(
    manifest: dict, business_base: dict | None, edition: str, version: str
) -> tuple[str, str] | None:
    """Return the (resolved_edition, resolved_version) catalog to serve, or None.

    community (or unknown/empty edition): floor-resolve against the manifest.
    business: map version -> Community base via business_base, then floor-resolve THAT in the
    manifest. A Business device is always served a Community catalog (the shared core).
    """
    community = _community_versions(manifest)
    if (edition or "community").lower() == "business":
        bmap = (business_base or {}).get("map", {})
        be = resolve_version(list(bmap), version)
        if be is None:
            return None
        cv = resolve_version(community, bmap[be])
        return ("community", cv) if cv else None
    cv = resolve_version(community, version)
    return ("community", cv) if cv else None


logger = logging.getLogger(__name__)
_HTTP_TIMEOUT = 15.0


async def _cache_get(session: AsyncSession, edition: str, version: str) -> CatalogCache | None:
    return (
        await session.execute(
            select(CatalogCache).where(
                CatalogCache.edition == edition, CatalogCache.version == version
            )
        )
    ).scalar_one_or_none()


async def get_catalog(
    session: AsyncSession,
    edition: str,
    version: str,
    *,
    base_url: str | None = None,
    auto_fetch: bool | None = None,
) -> dict | None:
    """Resolve the device's (edition, version) to a published catalog, verify + cache, and return it.

    base_url/auto_fetch default to settings; callers (the API) omit them. Returns None when no catalog
    can be resolved (network down + nothing cached, SHA mismatch, or no version <= the device's).
    """
    settings = get_settings()
    base = (base_url if base_url is not None else settings.catalog_release_base_url).rstrip("/")
    fetch = settings.catalog_auto_fetch if auto_fetch is None else auto_fetch
    edition = (edition or "community").lower()

    target: tuple[str, str] | None = None
    if fetch:
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as http:
                manifest = (await http.get(f"{base}/manifest.json")).raise_for_status().json()
                business_base = None
                if edition == "business":
                    business_base = (
                        await http.get(f"{base}/business-base.json")
                    ).raise_for_status().json()
                target = resolve_target(manifest, business_base, edition, version)
                if target is not None:
                    res_ed, res_ver = target
                    row = await _cache_get(session, res_ed, res_ver)
                    if row is not None:
                        return row.content
                    expected = manifest.get("catalogs", {}).get(f"{res_ed}/{res_ver}")
                    raw = (
                        await http.get(f"{base}/{res_ed}-{res_ver}.json")
                    ).raise_for_status().content
                    actual = hashlib.sha256(raw).hexdigest()
                    # Fail closed: a missing manifest entry is NOT a pass — a tampered manifest that
                    # drops the key while serving a malicious catalog must be rejected, not cached.
                    if not expected:
                        logger.warning(
                            "catalog sha256 missing in manifest for %s/%s — rejected",
                            res_ed, res_ver)
                    elif actual != expected:
                        logger.warning(
                            "catalog sha256 mismatch for %s/%s — rejected", res_ed, res_ver)
                    else:
                        content = json.loads(raw)
                        session.add(CatalogCache(
                            edition=res_ed, version=res_ver, sha256=actual, content=content))
                        await session.flush()
                        return content
        except (httpx.HTTPError, ValueError, KeyError):
            pass  # fall through to the offline fallback

    # Offline / failed fallback: probe the cache for the resolved identity if known, else the raw one.
    if target is not None:
        row = await _cache_get(session, target[0], target[1])
        if row is not None:
            return row.content
    row = await _cache_get(session, edition, version)
    return row.content if row is not None else None


async def get_model(
    session: AsyncSession,
    edition: str,
    version: str,
    model_id: str,
    *,
    base_url: str | None = None,
    auto_fetch: bool | None = None,
) -> dict | None:
    """Convenience: the named model from the device's catalog (or None)."""
    catalog = await get_catalog(
        session, edition, version, base_url=base_url, auto_fetch=auto_fetch)
    if catalog is None:
        return None
    return catalog.get("models", {}).get(model_id)
