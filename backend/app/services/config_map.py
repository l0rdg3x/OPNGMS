"""Annotate a build_tree config.xml tree with catalog coverage (read-only cross-reference).

Each node is tagged editable/read-only: a node whose config.xml path falls under a catalog model's
`xml_path` mount is editable-via-that-model; everything else is read-only (legacy / non-MVC, no API)."""
from __future__ import annotations

import re

_INDEX = re.compile(r"\[\d+\]$")


def _norm(path: str) -> str:
    """Lowercase, strip [n] index suffixes from each segment — for prefix matching."""
    return "/".join(_INDEX.sub("", seg) for seg in path.lower().split("/"))


def _model_mounts(catalog: dict) -> list[tuple[str, str]]:
    """(normalised xml_path, model_id), longest-path first so the most specific model wins."""
    out = []
    for mid, m in (catalog.get("models", {}) or {}).items():
        xp = m.get("xml_path")
        if xp:
            out.append((_norm(xp), mid))
    out.sort(key=lambda t: len(t[0]), reverse=True)
    return out


def _covering_model(norm_path: str, mounts: list[tuple[str, str]]) -> str | None:
    for mount, mid in mounts:
        if norm_path == mount or norm_path.startswith(mount + "/"):
            return mid
    return None


def annotate_with_catalog(tree: dict, catalog: dict) -> dict:
    """Return a deep copy of `tree` with `editable: bool` and (when editable) `catalog_model_id` set."""
    mounts = _model_mounts(catalog)

    def walk(node: dict) -> dict:
        mid = _covering_model(_norm(node.get("path", "")), mounts)
        new = {**node}
        new["editable"] = mid is not None
        if mid is not None:
            new["catalog_model_id"] = mid
        new["children"] = [walk(c) for c in node.get("children", [])]
        return new

    return walk(tree)
