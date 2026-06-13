"""Report data model: plain dataclasses assembled from aggregations, rendered by the template."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime


def human_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    f, i = float(n), 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024
        i += 1
    return f"{f:.1f} {units[i]}"


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
class WebActivityBlock:
    timeline_svg: str
    top_sites: RankedTable
    top_initiators: RankedTable
    top_blocked: RankedTable


@dataclass
class BandwidthBlock:
    timeline_svg: str
    total_in: str   # human-formatted
    total_out: str


@dataclass
class StatusBlock:
    timeline_svg: str
    uptime_pct: float


@dataclass
class ThreatRow:
    label: str
    count: int
    level: str   # controlled enum: "low" | "guarded" | "high"


@dataclass
class ThreatRankedTable:
    title: str
    columns: tuple[str, str]          # (label header, count header); a "Threat" column is implicit
    rows: list[ThreatRow]


@dataclass
class ApplicationsBlock:
    timeline_svg: str
    top_detected: ThreatRankedTable
    top_blocked: ThreatRankedTable
    top_categories: ThreatRankedTable
    top_initiators: RankedTable
    sample: bool = True


@dataclass
class WebFilterBlock:
    timeline_svg: str
    top_categories: ThreatRankedTable
    top_sites: RankedTable
    top_initiators: RankedTable
    sample: bool = True


@dataclass
class DeviceSection:
    device_name: str
    attacks: AttacksBlock | None = None
    web: WebActivityBlock | None = None
    bandwidth: BandwidthBlock | None = None
    status: StatusBlock | None = None
    applications: ApplicationsBlock | None = None
    web_filter: WebFilterBlock | None = None


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
    logo_data_uri: str | None = None
    t: ReportText | None = None
    locale: str = "en"

    def __post_init__(self) -> None:
        # The template always dereferences ctx.t; default to English so any ReportContext renders.
        if self.t is None:
            from app.services.reporting.i18n import report_text

            self.t = report_text("en")

    @property
    def toc(self) -> list[str]:
        return [s.device_name for s in self.sections]

    @property
    def is_rtl(self) -> bool:
        # Drives the template's dir="rtl" (+ CSS direction) for right-to-left languages (e.g. Arabic).
        from app.services.reporting.i18n import is_rtl

        return is_rtl(self.locale)

    @property
    def dir(self) -> str:
        return "rtl" if self.is_rtl else "ltr"


from datetime import UTC  # noqa: E402

from app.services.reporting.aggregation import ReportAggregator, pick_bucket  # noqa: E402
from app.services.reporting.charts import line_chart  # noqa: E402
from app.services.reporting.i18n import ReportText, report_text  # noqa: E402


async def build_context(
    aggregator: ReportAggregator,
    *,
    tenant_name: str,
    timezone_name: str,
    owner: str | None,
    frm: datetime,
    to: datetime,
    title: str = "Security & Activity Report",
    logo_data_uri: str | None = None,
    locale: str = "en",
    device_id: uuid.UUID | None = None,
) -> ReportContext:
    # Local import: mock_sections imports the dataclasses from this module, so importing it here
    # (rather than at module top) avoids a circular-import cycle and lets mock_sections be imported
    # standalone. Swapped for a real aggregator when app-id/category ingest lands.
    from app.services.reporting.mock_sections import applications_block, web_filter_block
    t = report_text(locale)

    def _ud(v: float) -> str:  # locale-aware up/down formatter for the availability chart (closes over t)
        return t.status_up if v >= 0.99 else (t.status_down if v <= 0.01 else "")

    bucket = pick_bucket(to - frm)
    sections: list[DeviceSection] = []
    devices = await aggregator.devices(device_id=device_id)
    for dev in devices:
        # Attacks block: timeline + three ranked tables (IDS), per-device.
        tl = await aggregator.timeline(frm=frm, to=to, bucket=bucket, source="ids", device_id=dev.id)
        svg = line_chart(
            [(b.astimezone(UTC).strftime("%m-%d %H:%M"), c) for b, c in tl],
            width=520,
            height=140,
            y_label=t.axis_attempts,
            x_label=t.axis_time,
            empty_text=t.no_data,
        )
        top_attempts = await aggregator.top(field="name", frm=frm, to=to, device_id=dev.id)
        top_targets = await aggregator.top(field="dst_ip", frm=frm, to=to, device_id=dev.id)
        top_initiators = await aggregator.top(field="src_ip", frm=frm, to=to, device_id=dev.id)
        attacks = AttacksBlock(
            timeline_svg=svg,
            tables=[
                RankedTable(t.t_top_attempts, (t.col_signature, t.col_count), [(r.value, r.count) for r in top_attempts]),
                RankedTable(t.t_top_targets, (t.col_target, t.col_count), [(r.value, r.count) for r in top_targets]),
                RankedTable(t.t_top_initiators, (t.col_initiator, t.col_count), [(r.value, r.count) for r in top_initiators]),
            ],
        )

        # --- Web Activity (DNS) ---
        dns_tl = await aggregator.timeline(frm=frm, to=to, bucket=bucket, source="dns", device_id=dev.id)
        web = WebActivityBlock(
            timeline_svg=line_chart([(b.astimezone(UTC).strftime("%m-%d %H:%M"), c) for b, c in dns_tl], width=520, height=140, y_label=t.axis_dns, x_label=t.axis_time, empty_text=t.no_data),
            top_sites=RankedTable(t.t_top_sites, (t.col_site, t.col_hits),
                                  [(r.value, r.count) for r in await aggregator.top(field="name", source="dns", frm=frm, to=to, device_id=dev.id)]),
            top_initiators=RankedTable(t.t_top_initiators, (t.col_initiator, t.col_hits),
                                       [(r.value, r.count) for r in await aggregator.top(field="src_ip", source="dns", frm=frm, to=to, device_id=dev.id)]),
            top_blocked=RankedTable(t.t_top_blocked, (t.col_domain, t.col_blocks),
                                    [(r.value, r.count) for r in await aggregator.top_blocked_domains(frm=frm, to=to, device_id=dev.id)]),
        )

        # --- Data Usage (bandwidth) ---
        bw_tl = await aggregator.bandwidth_timeline(frm=frm, to=to, bucket=bucket, device_id=dev.id)
        tin, tout = await aggregator.bandwidth_totals(frm=frm, to=to, bucket=bucket, device_id=dev.id)
        bandwidth = BandwidthBlock(
            timeline_svg=line_chart([(b.astimezone(UTC).strftime("%m-%d %H:%M"), v) for b, v in bw_tl], width=520, height=140, y_label=t.axis_data, x_label=t.axis_time, y_format=human_bytes, empty_text=t.no_data),
            total_in=human_bytes(tin), total_out=human_bytes(tout),
        )

        # --- Up/Down status ---
        av_series, uptime = await aggregator.availability_series(frm=frm, to=to, bucket=bucket, device_id=dev.id)
        status = StatusBlock(
            timeline_svg=line_chart([(b.astimezone(UTC).strftime("%m-%d %H:%M"), v) for b, v in av_series], width=520, height=80, y_label=t.axis_status, x_label=t.axis_time, y_format=_ud, empty_text=t.no_data),
            uptime_pct=round(uptime, 1),
        )

        # --- Applications + Web Filter (deterministic MOCK; labeled as sample data in the template) ---
        applications = applications_block(dev.name, t)
        web_filter = web_filter_block(dev.name, t)

        sections.append(DeviceSection(
            device_name=dev.name, attacks=attacks, web=web, bandwidth=bandwidth, status=status,
            applications=applications, web_filter=web_filter,
        ))

    return ReportContext(
        tenant_name=tenant_name,
        title=title,
        timezone=timezone_name,
        owner=owner,
        range_from=frm,
        range_to=to,
        sections=sections,
        logo_data_uri=logo_data_uri,
        t=t,
        locale=locale,
    )
