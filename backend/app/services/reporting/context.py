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
class ExecutiveSummaryBlock:
    """Report-level KPI band (client-facing), rendered once at the top after the TOC."""
    devices_total: int
    devices_online: int
    attacks_blocked: int
    data_total: str          # human-formatted bytes
    uptime_pct: float
    alerts_count: int


@dataclass
class CountryRow:
    """One ranked attacker-country row; `name` is already localized for the report locale."""
    name: str
    count: int
    pct: float


@dataclass
class AttackerCountriesBlock:
    """Report-level (tenant-wide) breakdown of blocked attacker IPs by resolved country."""
    rows: list[CountryRow]


@dataclass
class HealthBlock:
    cpu_avg: float | None
    cpu_peak: float | None
    mem_avg: float | None
    mem_peak: float | None
    disk_avg: float | None
    disk_peak: float | None
    sparkline_svg: str
    has_data: bool


@dataclass
class GatewayRow:
    name: str
    rtt_ms: float | None
    loss_pct: float | None
    up_pct: float


@dataclass
class VpnRow:
    name: str
    up_pct: float


@dataclass
class AlertItem:
    title: str               # "<type> <label>" already combined
    severity: str            # controlled enum: "info" | "warning" | "critical"
    severity_label: str      # localized severity text
    opened: str              # formatted datetime
    duration: str            # human duration, or the localized "ongoing"


@dataclass
class AlertsWanBlock:
    alerts: list[AlertItem]
    gateways: list[GatewayRow]
    vpns: list[VpnRow]


@dataclass
class ConfigChangeItem:
    summary: str             # "<kind> · <operation> · <target>"
    applied: str             # formatted datetime


@dataclass
class FirmwareConfigBlock:
    firmware_version: str
    edition: str
    series: str
    change_count: int
    changes: list[ConfigChangeItem]


@dataclass
class DeviceSection:
    device_name: str
    health: HealthBlock | None = None
    alerts_wan: AlertsWanBlock | None = None
    attacks: AttacksBlock | None = None
    web: WebActivityBlock | None = None
    bandwidth: BandwidthBlock | None = None
    status: StatusBlock | None = None
    firmware_config: FirmwareConfigBlock | None = None
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
    summary: ExecutiveSummaryBlock | None = None
    attacker_countries: AttackerCountriesBlock | None = None
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

from app.services.geoip import PRIVATE, UNKNOWN, GeoIp, localized_country_name  # noqa: E402
from app.services.reporting.aggregation import ReportAggregator, pick_bucket  # noqa: E402
from app.services.reporting.charts import line_chart  # noqa: E402
from app.services.reporting.i18n import ReportText, report_text  # noqa: E402


def _fmt_dt(dt: datetime | None, tzname: str) -> str:
    """Format a datetime in the report timezone (falls back to UTC on a bad tz)."""
    if dt is None:
        return ""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    try:
        tz = ZoneInfo(tzname)
    except (ZoneInfoNotFoundError, ValueError):
        tz = UTC
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _fmt_duration(start: datetime, end: datetime | None, ongoing: str) -> str:
    """Human 'Nd Nh Nm' duration between start and end; the localized 'ongoing' when unresolved."""
    if end is None:
        return ongoing
    secs = max(0, int((end - start).total_seconds()))
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


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
    sections_enabled: dict[str, bool] | None = None,
    geoip: GeoIp | None = None,
) -> ReportContext:
    # Local import: mock_sections imports the dataclasses from this module, so importing it here
    # (rather than at module top) avoids a circular-import cycle and lets mock_sections be imported
    # standalone. Swapped for a real aggregator when app-id/category ingest lands.
    from app.services.metric_labels import device_friendly_labels
    from app.services.reporting.mock_sections import applications_block, web_filter_block
    from app.services.reporting.sections import resolve_sections

    enabled = resolve_sections(sections_enabled, None) if sections_enabled is not None else resolve_sections(None, None)
    t = report_text(locale)

    def _ud(v: float) -> str:  # locale-aware up/down formatter for the availability chart (closes over t)
        return t.status_up if v >= 0.99 else (t.status_down if v <= 0.01 else "")

    def _sev(severity: str) -> tuple[str, str]:  # (controlled-enum class, localized label)
        s = severity.lower()
        if s in ("critical", "error", "high"):
            return "critical", t.sev_critical
        if s in ("info", "informational", "notice"):
            return "info", t.sev_info
        return "warning", t.sev_warning

    bucket = pick_bucket(to - frm)
    sections: list[DeviceSection] = []
    devices = await aggregator.devices(device_id=device_id)

    # Report-level executive summary (built once, from tenant-wide aggregates).
    summary: ExecutiveSummaryBlock | None = None
    if enabled["summary"]:
        k = await aggregator.kpis(frm=frm, to=to, bucket=bucket)
        summary = ExecutiveSummaryBlock(
            devices_total=k.devices_total, devices_online=k.devices_online,
            attacks_blocked=k.attacks_blocked, data_total=human_bytes(k.data_total),
            uptime_pct=k.uptime_pct, alerts_count=k.alerts_count,
        )

    # Report-level attacker-countries breakdown (tenant-wide, like the executive summary — built once,
    # ignores device_id). Degrades to None when no mmdb is loadable so the section simply doesn't render.
    attacker_countries: AttackerCountriesBlock | None = None
    if enabled["attacker_countries"]:
        from app.services.geoip_provider import get_geoip  # local import: avoids an import cycle

        gi = geoip if geoip is not None else await get_geoip(aggregator.session)
        if gi is not None:
            country_counts = await aggregator.attacker_countries(frm=frm, to=to, geoip=gi, limit=15)
            rows = [
                CountryRow(
                    name=(
                        t.country_private if c.code == PRIVATE
                        else t.country_unknown if c.code == UNKNOWN
                        else localized_country_name(c.code, locale)
                    ),
                    count=c.count,
                    pct=c.pct,
                )
                for c in country_counts
            ]
            attacker_countries = AttackerCountriesBlock(rows=rows)

    for dev in devices:
        # --- Device health (CPU/mem/disk avg+peak + cpu sparkline) ---
        health: HealthBlock | None = None
        if enabled["health"]:
            hs = await aggregator.health_summary(frm=frm, to=to, bucket=bucket, device_id=dev.id)
            spark = line_chart(
                [(b.astimezone(UTC).strftime("%m-%d %H:%M"), v) for b, v in hs.cpu_series],
                width=520, height=80, y_label=t.health_cpu, x_label=t.axis_time, empty_text=t.no_data,
            )
            health = HealthBlock(
                cpu_avg=hs.cpu.avg, cpu_peak=hs.cpu.peak, mem_avg=hs.mem.avg, mem_peak=hs.mem.peak,
                disk_avg=hs.disk.avg, disk_peak=hs.disk.peak, sparkline_svg=spark, has_data=hs.has_data,
            )

        # --- Alerts + WAN/gateway quality + VPN ---
        alerts_wan: AlertsWanBlock | None = None
        if enabled["alerts_wan"]:
            raw_alerts = await aggregator.alerts_in_range(frm=frm, to=to, device_id=dev.id)
            gws = await aggregator.gateway_quality(frm=frm, to=to, device_id=dev.id)
            vpns = await aggregator.vpn_status(frm=frm, to=to, device_id=dev.id)
            # Show the assigned gateway/VPN names (from the device config) instead of raw ids — the
            # same mapping the Health dashboard uses; falls back to the raw name when none is set.
            labels = await device_friendly_labels(aggregator.session, aggregator.tenant_id, dev.id)
            alert_items = []
            for a in raw_alerts:
                sev_cls, sev_lbl = _sev(a.severity)
                title_txt = f"{a.type} {a.label}".strip() if a.label else a.type
                alert_items.append(AlertItem(
                    title=title_txt, severity=sev_cls, severity_label=sev_lbl,
                    opened=_fmt_dt(a.opened_at, timezone_name),
                    duration=_fmt_duration(a.opened_at, a.resolved_at, t.duration_ongoing),
                ))
            alerts_wan = AlertsWanBlock(
                alerts=alert_items,
                gateways=[GatewayRow(name=labels.get(g.name, g.name), rtt_ms=g.rtt_ms, loss_pct=g.loss_pct, up_pct=g.up_pct) for g in gws],
                vpns=[VpnRow(name=labels.get(v.name, v.name), up_pct=v.up_pct) for v in vpns],
            )

        # --- Attacks block (IDS): timeline + three ranked tables ---
        attacks: AttacksBlock | None = None
        if enabled["attacks"]:
            tl = await aggregator.timeline(frm=frm, to=to, bucket=bucket, source="ids", device_id=dev.id)
            svg = line_chart(
                [(b.astimezone(UTC).strftime("%m-%d %H:%M"), c) for b, c in tl],
                width=520, height=140, y_label=t.axis_attempts, x_label=t.axis_time, empty_text=t.no_data,
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
        web: WebActivityBlock | None = None
        if enabled["web"]:
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
        bandwidth: BandwidthBlock | None = None
        if enabled["data"]:
            bw_tl = await aggregator.bandwidth_timeline(frm=frm, to=to, bucket=bucket, device_id=dev.id)
            tin, tout = await aggregator.bandwidth_totals(frm=frm, to=to, bucket=bucket, device_id=dev.id)
            bandwidth = BandwidthBlock(
                timeline_svg=line_chart([(b.astimezone(UTC).strftime("%m-%d %H:%M"), v) for b, v in bw_tl], width=520, height=140, y_label=t.axis_data, x_label=t.axis_time, y_format=human_bytes, empty_text=t.no_data),
                total_in=human_bytes(tin), total_out=human_bytes(tout),
            )

        # --- Up/Down status ---
        status: StatusBlock | None = None
        if enabled["status"]:
            av_series, uptime = await aggregator.availability_series(frm=frm, to=to, bucket=bucket, device_id=dev.id)
            status = StatusBlock(
                timeline_svg=line_chart([(b.astimezone(UTC).strftime("%m-%d %H:%M"), v) for b, v in av_series], width=520, height=80, y_label=t.axis_status, x_label=t.axis_time, y_format=_ud, empty_text=t.no_data),
                uptime_pct=round(uptime, 1),
            )

        # --- Firmware + config changes ---
        firmware_config: FirmwareConfigBlock | None = None
        if enabled["firmware_config"]:
            count, changes = await aggregator.config_changes_in_range(frm=frm, to=to, device_id=dev.id)
            firmware_config = FirmwareConfigBlock(
                firmware_version=dev.firmware_version or "—", edition=dev.edition or "—",
                series=dev.firmware_series or "—", change_count=count,
                changes=[
                    ConfigChangeItem(
                        summary=" · ".join(p for p in (c.kind, c.operation, c.target) if p),
                        applied=_fmt_dt(c.applied_at, timezone_name),
                    )
                    for c in changes
                ],
            )

        # --- Applications + Web Filter (deterministic MOCK; labeled as sample data in the template) ---
        applications = applications_block(dev.name, t) if enabled["applications"] else None
        web_filter = web_filter_block(dev.name, t) if enabled["web_filter"] else None

        sections.append(DeviceSection(
            device_name=dev.name, health=health, alerts_wan=alerts_wan, attacks=attacks, web=web,
            bandwidth=bandwidth, status=status, firmware_config=firmware_config,
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
        summary=summary,
        attacker_countries=attacker_countries,
        logo_data_uri=logo_data_uri,
        t=t,
        locale=locale,
    )
