"""Deterministic, per-device MOCK providers for the Applications and Web Filter report sections.

No real app-id/flow/content-categorization is ingested yet; these blocks are clearly labeled as sample
data in the template. Output is deterministic (seeded by device name) so it is stable and testable, and
distinct per device. When a real feed lands, swap these for a real aggregator with the same block shape.
"""
from __future__ import annotations

import hashlib

from app.services.reporting.charts import line_chart
from app.services.reporting.context import (
    ApplicationsBlock,
    RankedTable,
    ThreatRankedTable,
    ThreatRow,
    WebFilterBlock,
)

# Fixed palettes with a fixed threat level each (controlled enum values only).
_APPS = [
    ("Microsoft 365", "low"), ("Zoom", "low"), ("WhatsApp", "low"),
    ("Dropbox", "guarded"), ("TikTok", "guarded"), ("Steam", "guarded"),
    ("BitTorrent", "high"), ("Tor", "high"), ("TeamViewer", "high"),
]
_CATEGORIES = [
    ("Business", "low"), ("Streaming Media", "guarded"), ("Social Networking", "guarded"),
    ("File Sharing", "high"), ("Gaming", "guarded"), ("Advertising", "guarded"),
    ("Malware", "high"), ("News", "low"),
]
_SITES = ["cdn.jsdelivr.net", "news.example.com", "ads.doubleclick.net", "drive.google.com", "facebook.com", "github.com"]
_INITIATORS = ["10.0.0.10", "10.0.0.21", "10.0.0.42", "10.0.0.55", "10.0.0.73"]


def _seed(name: str) -> int:
    # Stable across processes (unlike hash()); PYTHONHASHSEED-independent.
    return int.from_bytes(hashlib.sha1(name.encode("utf-8")).digest()[:4], "big")


def _rotate(items: list, seed: int) -> list:
    if not items:
        return items
    k = seed % len(items)
    return items[k:] + items[:k]


def _counts(seed: int, n: int) -> list[int]:
    base = 40 + (seed % 160)               # per-device magnitude
    return [max(1, base // (i + 1) + (seed >> (i % 5)) % 7) for i in range(n)]


def _timeline_svg(seed: int, *, height: int = 140, y_label: str = "Sessions") -> str:
    pts = [(f"t{i}", 5 + (seed >> (i % 6)) % 40 + (i % 3) * 3) for i in range(6)]
    return line_chart(pts, width=520, height=height, y_label=y_label, x_label="Time")


def _threat_table(title: str, columns: tuple[str, str], palette: list[tuple[str, str]], seed: int, n: int) -> ThreatRankedTable:
    rotated = _rotate(palette, seed)[:n]
    counts = _counts(seed, len(rotated))
    rows = [ThreatRow(label=label, count=c, level=level) for (label, level), c in zip(rotated, counts)]
    return ThreatRankedTable(title=title, columns=columns, rows=rows)


def _plain_table(title: str, columns: tuple[str, str], items: list[str], seed: int, n: int) -> RankedTable:
    rotated = _rotate(items, seed)[:n]
    counts = _counts(seed, len(rotated))
    return RankedTable(title=title, columns=columns, rows=list(zip(rotated, counts)))


def applications_block(device_name: str) -> ApplicationsBlock:
    seed = _seed(device_name)
    return ApplicationsBlock(
        timeline_svg=_timeline_svg(seed, y_label="Sessions"),
        top_detected=_threat_table("Top Detected", ("Application", "Sessions"), _APPS, seed, 5),
        top_blocked=_threat_table("Top Blocked", ("Application", "Blocks"), _rotate(_APPS, seed + 3), seed + 3, 4),
        top_categories=_threat_table("Top Categories", ("Category", "Sessions"), _CATEGORIES, seed, 5),
        top_initiators=_plain_table("Top Initiators", ("Initiator", "Sessions"), _INITIATORS, seed, 4),
    )


def web_filter_block(device_name: str) -> WebFilterBlock:
    seed = _seed(device_name) ^ 0x5F5F
    return WebFilterBlock(
        timeline_svg=_timeline_svg(seed, height=140, y_label="Requests"),
        top_categories=_threat_table("Top Categories", ("Category", "Requests"), _CATEGORIES, seed, 5),
        top_sites=_plain_table("Top Sites", ("Site", "Requests"), _SITES, seed, 5),
        top_initiators=_plain_table("Top Initiators", ("Initiator", "Requests"), _INITIATORS, seed, 4),
    )
