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
    ConfigAuditChangeRow,
    ConfigChangesBlock,
    ConfigChannelRow,
    RankedTable,
    ReliabilityBlock,
    ReliabilityCategoryRow,
    ReliabilityEventRow,
    ThreatRankedTable,
    ThreatRow,
    WebFilterBlock,
)
from app.services.reporting.i18n import ReportText

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


def _timeline_svg(seed: int, *, height: int = 140, y_label: str = "Sessions", x_label: str = "Time", empty_text: str = "No data") -> str:
    pts = [(f"t{i}", 5 + (seed >> (i % 6)) % 40 + (i % 3) * 3) for i in range(6)]
    return line_chart(pts, width=520, height=height, y_label=y_label, x_label=x_label, empty_text=empty_text)


def _threat_table(title: str, columns: tuple[str, str], palette: list[tuple[str, str]], seed: int, n: int) -> ThreatRankedTable:
    rotated = _rotate(palette, seed)[:n]
    counts = _counts(seed, len(rotated))
    rows = [ThreatRow(label=label, count=c, level=level) for (label, level), c in zip(rotated, counts, strict=False)]
    return ThreatRankedTable(title=title, columns=columns, rows=rows)


def _plain_table(title: str, columns: tuple[str, str], items: list[str], seed: int, n: int) -> RankedTable:
    rotated = _rotate(items, seed)[:n]
    counts = _counts(seed, len(rotated))
    return RankedTable(title=title, columns=columns, rows=list(zip(rotated, counts, strict=False)))


def applications_block(device_name: str, t: ReportText) -> ApplicationsBlock:
    seed = _seed(device_name)
    return ApplicationsBlock(
        timeline_svg=_timeline_svg(seed, y_label=t.axis_sessions, x_label=t.axis_time, empty_text=t.no_data),
        top_detected=_threat_table(t.t_top_detected, (t.col_application, t.col_sessions), _APPS, seed, 5),
        top_blocked=_threat_table(t.t_top_blocked, (t.col_application, t.col_blocks), _rotate(_APPS, seed + 3), seed + 3, 4),
        top_categories=_threat_table(t.t_top_categories, (t.col_category, t.col_sessions), _CATEGORIES, seed, 5),
        top_initiators=_plain_table(t.t_top_initiators, (t.col_initiator, t.col_sessions), _INITIATORS, seed, 4),
    )


def reliability_block(t: ReportText) -> ReliabilityBlock:
    """Deterministic sample reliability section for the demo/sample report. Report-level (tenant-wide),
    so it takes no device name. In production, build_context uses the real reliability aggregator; this
    mock lets a sample report render the section without seeded service events."""
    categories = [
        ReliabilityCategoryRow(label=t.rel_cat_service, count=4, pct=50.0),
        ReliabilityCategoryRow(label=t.rel_cat_disk, count=3, pct=37.5),
        ReliabilityCategoryRow(label=t.rel_cat_reboot, count=1, pct=12.5),
    ]
    events = [
        ReliabilityEventRow(
            time="2026-06-08 03:14", category=t.rel_cat_reboot, name="reboot",
            severity="critical", severity_label=t.sev_critical, device="fw-edge",
        ),
        ReliabilityEventRow(
            time="2026-06-07 21:02", category=t.rel_cat_service, name="service_crashed",
            severity="critical", severity_label=t.sev_critical, device="fw-edge",
        ),
        ReliabilityEventRow(
            time="2026-06-06 11:48", category=t.rel_cat_disk, name="filesystem_full",
            severity="warning", severity_label=t.sev_warning, device="fw-branch",
        ),
    ]
    return ReliabilityBlock(categories=categories, events=events, total=8)


def config_audit_block(t: ReportText) -> ConfigChangesBlock:
    """Deterministic sample config-changes section for the demo/sample report. Report-level
    (tenant-wide), so it takes no device name. In production, build_context uses the real config_audit
    aggregator; this mock lets a sample report render the section without seeded config_audit events.

    The direct/drift channels (gui/system) are emphasized — they are the on-box changes made outside
    the management API."""
    channels = [
        ConfigChannelRow(label=t.config_channel_api, count=4, pct=50.0, direct=False),
        ConfigChannelRow(label=t.config_channel_gui, count=3, pct=37.5, direct=True),
        ConfigChannelRow(label=t.config_channel_system, count=1, pct=12.5, direct=True),
    ]
    changes = [
        ConfigAuditChangeRow(
            time="2026-06-08 09:42", actor="admin@10.0.0.5", area="firewall",
            channel_label=t.config_channel_gui, direct=True, device="fw-edge",
        ),
        ConfigAuditChangeRow(
            time="2026-06-08 03:14", actor="root", area="firmware",
            channel_label=t.config_channel_system, direct=True, device="fw-edge",
        ),
        ConfigAuditChangeRow(
            time="2026-06-07 18:20", actor="root@192.168.6.100", area="monit",
            channel_label=t.config_channel_api, direct=False, device="fw-branch",
        ),
    ]
    return ConfigChangesBlock(channels=channels, changes=changes, total=8, direct=4)


def web_filter_block(device_name: str, t: ReportText) -> WebFilterBlock:
    seed = _seed(device_name) ^ 0x5F5F
    return WebFilterBlock(
        timeline_svg=_timeline_svg(seed, height=140, y_label=t.axis_requests, x_label=t.axis_time, empty_text=t.no_data),
        top_categories=_threat_table(t.t_top_categories, (t.col_category, t.col_requests), _CATEGORIES, seed, 5),
        top_sites=_plain_table(t.t_top_sites, (t.col_site, t.col_requests), _SITES, seed, 5),
        top_initiators=_plain_table(t.t_top_initiators, (t.col_initiator, t.col_requests), _INITIATORS, seed, 4),
    )
