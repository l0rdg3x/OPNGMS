"""Fetch + cache + verify the GeoIP country mmdb published as a GitHub Release asset.

Mirrors `catalog_provider`: on a cache-miss (and when `geoip_auto_fetch` is on) the app fetches the
mmdb + its SHA-256 manifest from the trusted `geoip` release URL over httpx, verifies the digest, and
stores the bytes+sha+version in the global non-RLS `geoip_cache` table (upsert by `source`). A built
`GeoIp` reader is process-cached and reused for subsequent lookups, so resolution is fully offline; the
cache is invalidated when the stored `version` changes. Any fetch/verify failure degrades to None
(IPs roll up as "Unknown") — it NEVER raises into the request path.
"""
from __future__ import annotations

import hashlib
import io
import logging

import httpx
import maxminddb
from maxminddb.const import MODE_FD
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.geoip_cache import GeoipCache
from app.services.geoip import GeoIp

logger = logging.getLogger(__name__)

_HTTP_TIMEOUT = 30.0  # the mmdb is a few MB — allow a bit more than the catalog JSON fetch
_MAX_MMDB_BYTES = 64 * 1024 * 1024  # the DB-IP Lite Country mmdb is ~6 MB; reject anything absurdly large
_SOURCE = "dbip-country"
_MANIFEST = "manifest.json"
_ASSET = "dbip-country.mmdb"

# Process-level cache: a built reader plus the cache `version` it was built from. Invalidated when the
# stored version changes. Module-global (one mmdb per process), reset via `clear_geoip_cache` in tests.
_cached: GeoIp | None = None
_cached_version: str | None = None


def clear_geoip_cache() -> None:
    """Drop the in-process reader (tests; also a hook for an explicit refresh)."""
    global _cached, _cached_version
    if _cached is not None:
        try:
            _cached.close()
        except Exception:  # closing a half-built reader must never raise
            logger.debug("error closing cached GeoIp reader", exc_info=True)
    _cached = None
    _cached_version = None


def _build(mmdb: bytes) -> GeoIp:
    """Build a GeoIp reader from raw mmdb bytes (MODE_FD reads the whole buffer into memory)."""
    return GeoIp(maxminddb.Reader(io.BytesIO(mmdb), mode=MODE_FD))


async def _cache_row(session: AsyncSession) -> GeoipCache | None:
    return (
        await session.execute(select(GeoipCache).where(GeoipCache.source == _SOURCE))
    ).scalar_one_or_none()


async def _fetch_and_store(session: AsyncSession, base: str) -> GeoipCache | None:
    """Fetch the mmdb + manifest from the release, verify SHA-256, upsert the cache row. None on failure."""
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as http:
            manifest = (await http.get(f"{base}/{_MANIFEST}")).raise_for_status().json()
            if not isinstance(manifest, dict):
                logger.warning("geoip manifest is not a JSON object — rejected")
                return None
            expected = manifest.get("sha256")
            version = manifest.get("version")
            raw = (await http.get(f"{base}/{_ASSET}")).raise_for_status().content
    except Exception:  # httpx errors (incl. InvalidURL), bad JSON, etc. — degrade, NEVER raise into the request
        logger.warning("geoip fetch failed", exc_info=True)
        return None
    # Fail closed: a missing sha in the manifest is NOT a pass (a tampered manifest that drops the key
    # while serving a malicious mmdb must be rejected, not cached).
    if not expected or not version:
        logger.warning("geoip manifest missing sha256/version — rejected")
        return None
    if len(raw) > _MAX_MMDB_BYTES:
        logger.warning("geoip mmdb exceeds the %d-byte cap — rejected", _MAX_MMDB_BYTES)
        return None
    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected:
        logger.warning("geoip mmdb sha256 mismatch — rejected")
        return None
    row = await _cache_row(session)
    if row is None:
        row = GeoipCache(source=_SOURCE, sha256=actual, mmdb=raw, version=version)
        session.add(row)
    else:
        row.sha256 = actual
        row.mmdb = raw
        row.version = version
    await session.flush()
    return row


async def get_geoip(session: AsyncSession) -> GeoIp | None:
    """Return a process-cached `GeoIp` reader, or None when no mmdb can be loaded (graceful degrade).

    Resolution order: the in-process reader (if its version matches the cache) → the cached row → an
    auto-fetch from the release (when `geoip_auto_fetch`). Any failure returns None — never raises.
    """
    global _cached, _cached_version
    settings = get_settings()

    row = await _cache_row(session)
    if row is None and settings.geoip_auto_fetch:
        base = settings.geoip_release_base_url.rstrip("/")
        row = await _fetch_and_store(session, base)
    if row is None:
        return None

    # Serve the process-cached reader unless the cache version moved (a newer mmdb was published).
    if _cached is not None and _cached_version == row.version:
        return _cached
    try:
        reader = _build(row.mmdb)
    except Exception:  # a corrupt cached blob must degrade to "no data", not 500 the request
        logger.warning("failed to open cached geoip mmdb", exc_info=True)
        return None
    clear_geoip_cache()
    _cached = reader
    _cached_version = row.version
    return _cached
