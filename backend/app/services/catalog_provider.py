"""Fetch + cache + verify versioned OPNsense catalogs published as GitHub Release assets.

A Business device is served the Community catalog of its base version (resolve_target maps it via
business-base.json). The catalog file's SHA-256 is verified against the manifest before it is cached
or used. Offline, a previously-cached catalog is still served; a cold offline start returns None.
"""
from __future__ import annotations

import re

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
