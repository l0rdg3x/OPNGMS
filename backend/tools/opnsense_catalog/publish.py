from __future__ import annotations

import hashlib


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of raw bytes (the integrity check the provider re-verifies)."""
    return hashlib.sha256(data).hexdigest()


def build_manifest(entries: dict[str, bytes]) -> dict:
    """entries maps "edition/version" -> the catalog file's exact bytes.

    Returns {"catalogs": {"edition/version": "<sha256-hex>"}}. The CLI adds `generated_at`.
    """
    return {"catalogs": {key: sha256_hex(blob) for key, blob in entries.items()}}
