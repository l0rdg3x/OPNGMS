"""Report data model: plain dataclasses assembled from aggregations, rendered by the template."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RankedTable:
    title: str
    columns: tuple[str, str]          # e.g. ("Signature", "Count")
    rows: list[tuple[str, int]]       # already escaped at render time by autoescape


@dataclass
class AttacksBlock:
    timeline_svg: str                 # SVG string (built from escaped values) — marked safe at render
    tables: list[RankedTable]


@dataclass
class DeviceSection:
    device_name: str
    attacks: AttacksBlock | None = None


@dataclass
class ReportContext:
    # branding placeholders (5D fills these from per-tenant white-label config)
    tenant_name: str
    title: str
    timezone: str
    owner: str | None
    range_from: datetime
    range_to: datetime
    sections: list[DeviceSection] = field(default_factory=list)

    @property
    def toc(self) -> list[str]:
        return [s.device_name for s in self.sections]
