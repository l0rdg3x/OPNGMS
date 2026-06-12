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
