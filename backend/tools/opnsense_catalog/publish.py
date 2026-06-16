from __future__ import annotations

import hashlib
import re


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of raw bytes (the integrity check the provider re-verifies)."""
    return hashlib.sha256(data).hexdigest()


def build_manifest(entries: dict[str, bytes]) -> dict:
    """entries maps "edition/version" -> the catalog file's exact bytes.

    Returns {"catalogs": {"edition/version": "<sha256-hex>"}}. The CLI adds `generated_at`.
    """
    return {"catalogs": {key: sha256_hex(blob) for key, blob in entries.items()}}


# Each opnsense/changelog `business/` file's header states what the release is based on — EITHER a
# Community version ("based on the OPNsense X.Y.Z community version") OR, for a Business hotfix, the
# PRIOR Business version ("based on the OPNsense X.Y.Z business version"). The kind is captured so a
# business-on-business chain can be followed transitively to the underlying Community base.
_BASE_RE = re.compile(
    r"based on the OPNsense\s+(\d+\.\d+(?:\.\d+)?)\s+(community|business)", re.IGNORECASE
)
_MAX_CHAIN = 32  # safety cap when following a business-on-business chain (also breaks cycles)


def parse_business_base(pages: dict[str, str]) -> dict:
    """pages maps a Business version -> the text of its release notes (an opnsense/changelog
    `business/<major>/<subversion>` file, one entry per sub-version).

    Each entry is resolved to its **Community** base version: a `community` header is the base
    directly; a `business` header (a hotfix chained on the prior Business release) is followed
    transitively until a Community base is reached. Entries with no marker, an unresolvable chain
    (a referenced Business version not in `pages`), or a cycle are skipped (never guess). Returns
    {"map": {business_version: community_base_version}}.
    """
    # Pass 1: raw {business_version: (referenced_version, "community"|"business")}.
    raw: dict[str, tuple[str, str]] = {}
    for be_version, text in pages.items():
        m = _BASE_RE.search(text or "")
        if m:
            raw[be_version] = (m.group(1), m.group(2).lower())

    # Pass 2: resolve each to a Community base, following business->business links.
    def _community_base(version: str) -> str | None:
        seen: set[str] = set()
        cur = version
        for _ in range(_MAX_CHAIN):
            if cur not in raw or cur in seen:
                return None  # dead-ends on an unknown version, or a cycle
            seen.add(cur)
            ref, kind = raw[cur]
            if kind == "community":
                return ref
            cur = ref
        return None

    mapping = {v: base for v in raw if (base := _community_base(v)) is not None}
    return {"map": mapping}


# A git tag that is a release version: NN.NN or NN.NN.NN (drops non-version tags like "stable/26.1").
_RELEASE_TAG_RE = re.compile(r"\A\d+\.\d+(?:\.\d+)?\Z")


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in v.split("."))


def release_versions(tags: list[str], *, minimum: str | None = None) -> list[str]:
    """Filter raw `opnsense/core` git tags to release versions, sorted ascending.

    The Community version list = the core repo's release tags. Keeps only NN.NN / NN.NN.NN tags;
    `minimum` (e.g. "26.1") drops anything older so we don't generate catalogs for ancient releases.
    """
    out = [t for t in tags if _RELEASE_TAG_RE.match(t)]
    if minimum is not None:
        floor = _version_key(minimum)
        out = [t for t in out if _version_key(t) >= floor]
    return sorted(set(out), key=_version_key)
