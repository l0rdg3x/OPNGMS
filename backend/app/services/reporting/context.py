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


from datetime import timezone as _tz  # noqa: E402

from app.services.reporting.aggregation import ReportAggregator, pick_bucket  # noqa: E402
from app.services.reporting.charts import line_chart  # noqa: E402


async def build_context(
    aggregator: ReportAggregator,
    *,
    tenant_name: str,
    timezone_name: str,
    owner: str | None,
    frm: datetime,
    to: datetime,
    title: str = "Security & Activity Report",
) -> ReportContext:
    bucket = pick_bucket(to - frm)
    sections: list[DeviceSection] = []
    devices = await aggregator.devices()
    for dev in devices:
        # Attacks block: timeline + three ranked tables (IDS).
        # NOTE (5A tech debt): timeline/top aggregate the tenant's IDS events for the range,
        # not yet filtered per device. Per-device filtering is added in 5B.
        tl = await aggregator.timeline(frm=frm, to=to, bucket=bucket, source="ids")
        svg = line_chart(
            [(b.astimezone(_tz.utc).strftime("%m-%d %H:%M"), c) for b, c in tl],
            width=520,
            height=140,
        )
        top_attempts = await aggregator.top(field="name", frm=frm, to=to)
        top_targets = await aggregator.top(field="dst_ip", frm=frm, to=to)
        top_initiators = await aggregator.top(field="src_ip", frm=frm, to=to)
        attacks = AttacksBlock(
            timeline_svg=svg,
            tables=[
                RankedTable("Top Attempts", ("Signature", "Count"), [(r.value, r.count) for r in top_attempts]),
                RankedTable("Top Targets", ("Target", "Count"), [(r.value, r.count) for r in top_targets]),
                RankedTable("Top Initiators", ("Initiator", "Count"), [(r.value, r.count) for r in top_initiators]),
            ],
        )
        sections.append(DeviceSection(device_name=dev.name, attacks=attacks))

    return ReportContext(
        tenant_name=tenant_name,
        title=title,
        timezone=timezone_name,
        owner=owner,
        range_from=frm,
        range_to=to,
        sections=sections,
    )
