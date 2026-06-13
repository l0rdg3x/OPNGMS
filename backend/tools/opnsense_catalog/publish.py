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


# OPNsense BE release pages state: "based on the OPNsense X.Y.Z community version".
_BASE_RE = re.compile(r"based on the OPNsense\s+(\d+\.\d+(?:\.\d+)?)\s+community", re.IGNORECASE)


def parse_business_base(pages: dict[str, str]) -> dict:
    """pages maps a Business version -> its BE_<v>.html text.

    Extracts the Community base version from each page; pages without the marker are skipped
    (never guess). Returns {"map": {business_version: community_base_version}}.
    """
    mapping: dict[str, str] = {}
    for be_version, html in pages.items():
        m = _BASE_RE.search(html or "")
        if m:
            mapping[be_version] = m.group(1)
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
