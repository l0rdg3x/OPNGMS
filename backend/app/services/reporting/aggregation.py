"""Tenant-scoped report aggregations over the events/metrics hypertables (RLS + tenant filter)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.event import TOP_FIELDS
from app.schemas.event import EventTopRow
from app.services.geoip import UNKNOWN, GeoIp

# Allowlist of TimescaleDB time_bucket widths. `bucket` is interpolated into the SQL only after
# being checked against this set (asyncpg cannot bind a Python str as a PG interval), so the
# allowlist — not parameter binding — is what makes the interpolation injection-safe.
_BUCKETS = ("1 hour", "6 hours", "1 day")
_BUCKET_DELTAS = {"1 hour": timedelta(hours=1), "6 hours": timedelta(hours=6), "1 day": timedelta(days=1)}


def _bucket_delta(bucket: str) -> timedelta:
    if bucket not in _BUCKET_DELTAS:
        raise ValueError(f"bucket not allowed: {bucket}")
    return _BUCKET_DELTAS[bucket]


def pick_bucket(span: timedelta) -> str:
    if span <= timedelta(days=2):
        return "1 hour"
    if span <= timedelta(days=14):
        return "6 hours"
    return "1 day"


@dataclass
class DeviceRow:
    id: uuid.UUID
    name: str
    firmware_version: str | None = None
    edition: str = ""
    firmware_series: str = ""
    status: str = ""


@dataclass
class HealthStat:
    """avg + peak of a 0-100 percentage metric over the range (None when no samples)."""
    avg: float | None
    peak: float | None


@dataclass
class HealthSummary:
    cpu: HealthStat
    mem: HealthStat
    disk: HealthStat
    cpu_series: list[tuple[datetime, float]]  # bucketed avg cpu.pct, for the sparkline
    has_data: bool


@dataclass
class GatewayQuality:
    name: str
    rtt_ms: float | None    # avg round-trip time
    loss_pct: float | None  # avg packet loss
    up_pct: float           # availability over the range (0-100)


@dataclass
class VpnStatus:
    name: str
    up_pct: float           # share of polls the tunnel was up (0-100)


@dataclass
class AlertRow:
    type: str
    label: str
    severity: str
    opened_at: datetime
    resolved_at: datetime | None


@dataclass
class ConfigChangeRow:
    kind: str
    operation: str
    target: str
    applied_at: datetime | None


@dataclass
class CountryCount:
    """One row of the attacker-countries breakdown: ISO alpha-2 code (or a sentinel), count, share %."""
    code: str
    count: int
    pct: float


@dataclass
class PerimeterRow:
    """One attacker IP in the perimeter view: resolved country, cumulative count, last-seen, a label
    (the last attempted username for failed logins / the most-targeted port for firewall blocks)."""
    src_ip: str
    country: str
    count: int
    last_seen: datetime
    label: str


@dataclass
class ReliabilityCount:
    """One row of the reliability category breakdown: category (reboot/service/disk), count, share %."""
    category: str
    count: int
    pct: float


@dataclass
class ReliabilityEvent:
    """A notable reliability event for the timeline list: when, what, how bad, and on which device."""
    time: datetime
    category: str
    name: str
    severity: str
    device: str


@dataclass
class ReliabilityRollup:
    """Reliability rollup over the range: per-category counts (+ share %) and a recent-events list."""
    by_category: list[ReliabilityCount]
    notable: list[ReliabilityEvent]
    total: int


# Direct (drift) channels — a config change OPNGMS did not make: on-box via WebGUI (`gui`) or
# console/script (`system`, from the parser's `_classify_channel`), or an api change from a
# non-management IP (`api_external`, from ingest's `_attribute_mgmt_ip`). All are `severity='medium'`;
# an OPNGMS-made api change is `opngms`/`severity='info'`.
_CONFIG_DRIFT_CHANNELS = ("gui", "system", "api_external")


@dataclass
class ConfigChannelCount:
    """One row of the config-change by-channel breakdown: channel (api/gui/system), count, share %."""
    channel: str
    count: int
    pct: float


@dataclass
class ConfigChangeEvent:
    """A notable config change for the timeline list: when, who (actor), where (area), via which
    channel, whether it is a direct/drift change, and on which device."""
    time: datetime
    actor: str
    area: str
    channel: str
    direct: bool
    device: str


@dataclass
class ConfigAuditRollup:
    """Config-change rollup over the range: per-channel counts (+ share %), the direct/drift total, and
    a recent-changes list."""
    by_channel: list[ConfigChannelCount]
    notable: list[ConfigChangeEvent]
    total: int
    direct: int


def _perimeter_label(kind: str, detail: dict) -> str:
    """Display label for a perimeter attacker row from its rollup detail."""
    if kind == "firewall_block":
        ports = detail.get("top_ports") or []
        return str(ports[0]) if ports else ""
    return str(detail.get("last_username") or "")


@dataclass
class Kpis:
    devices_total: int
    devices_online: int
    attacks_blocked: int     # IDS events in the range
    data_total: float        # bytes in + out over the range
    uptime_pct: float        # fleet-wide poll-presence availability (0-100)
    alerts_count: int        # alerts raised in the range


class ReportAggregator:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def devices(self, *, device_id: uuid.UUID | None = None) -> list[DeviceRow]:
        sql = (
            "SELECT id, name, firmware_version, edition, firmware_series, status "
            "FROM devices WHERE tenant_id = :tid"
        )
        params: dict = {"tid": self.tenant_id}
        if device_id is not None:
            sql += " AND id = :did"
            params["did"] = device_id
        rows = (await self.session.execute(text(sql + " ORDER BY name"), params)).all()
        return [
            DeviceRow(
                id=r.id, name=r.name, firmware_version=r.firmware_version,
                edition=r.edition or "", firmware_series=r.firmware_series or "", status=r.status or "",
            )
            for r in rows
        ]

    async def _ranked(
        self, *, field: str, source: str, frm: datetime, to: datetime,
        device_id: uuid.UUID | None = None, action: str | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        # `field` MUST be allowlisted (it is interpolated as a column name); everything else is bound.
        if field not in TOP_FIELDS:
            raise ValueError(f"field not allowed: {field}")
        clauses = ["tenant_id = :tid", f"{field} <> ''", "source = :source", "time >= :frm", "time < :to"]
        params: dict = {"tid": self.tenant_id, "source": source, "frm": frm, "to": to, "limit": min(limit, 1000)}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        if action is not None:
            clauses.append("action = :action")
            params["action"] = action
        where = " AND ".join(clauses)
        sql = text(
            f"SELECT {field} AS value, count(*) AS count FROM events WHERE {where} "
            f"GROUP BY {field} ORDER BY count DESC, value LIMIT :limit"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [EventTopRow(value=str(r.value), count=int(r.count)) for r in rows]

    async def top(
        self, *, field: str, frm: datetime, to: datetime, source: str = "ids",
        device_id: uuid.UUID | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        return await self._ranked(field=field, source=source, frm=frm, to=to, device_id=device_id, limit=limit)

    async def top_blocked_domains(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID | None = None, limit: int = 10,
    ) -> list[EventTopRow]:
        return await self._ranked(
            field="name", source="dns", frm=frm, to=to, device_id=device_id, action="blocked", limit=limit,
        )

    async def timeline(
        self, *, frm: datetime, to: datetime, bucket: str, source: str = "ids",
        device_id: uuid.UUID | None = None,
    ) -> list[tuple[datetime, int]]:
        if bucket not in _BUCKETS:
            raise ValueError(f"bucket not allowed: {bucket}")
        clauses = ["tenant_id = :tid", "source = :source", "time >= :frm", "time < :to"]
        params: dict = {"tid": self.tenant_id, "source": source, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        # `bucket` is allowlist-validated above; interpolated as a literal interval (asyncpg cannot
        # bind a str as an interval). Everything else is a bound parameter.
        sql = text(
            f"SELECT time_bucket('{bucket}'::interval, time) AS b, count(*) AS c "
            f"FROM events WHERE {where} GROUP BY b ORDER BY b"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [(r.b, int(r.c)) for r in rows]

    async def bandwidth_timeline(
        self, *, frm: datetime, to: datetime, bucket: str, device_id: uuid.UUID | None = None,
    ) -> list[tuple[datetime, float]]:
        """Transferred bytes (in+out) per bucket. Counters are cumulative, so per (bucket, interface,
        direction) we take max-min (clamped >= 0 defensively). A counter reset between buckets is handled
        correctly (each bucket only sees its own samples); a reset within a single bucket would overestimate
        that one bucket — acceptable given the poll cadence vs bucket width (a lag()-based delta is 5B debt)."""
        delta = _bucket_delta(bucket)  # bound as a real interval (timedelta)
        clauses = ["tenant_id = :tid", "metric IN ('iface.bytes_in','iface.bytes_out')", "time >= :frm", "time < :to"]
        params: dict = {"bucket": delta, "tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        sql = text(
            "SELECT b, SUM(d) AS total FROM ("
            "  SELECT time_bucket(:bucket, time) AS b, device_id, label, metric, "
            "         GREATEST(max(value) - min(value), 0) AS d "
            f"  FROM metrics WHERE {where} "
            "  GROUP BY b, device_id, label, metric"
            ") s GROUP BY b ORDER BY b"
        )
        rows = (await self.session.execute(sql, params)).all()
        return [(r.b, float(r.total)) for r in rows]

    async def bandwidth_totals(
        self, *, frm: datetime, to: datetime, bucket: str = "1 hour", device_id: uuid.UUID | None = None,
    ) -> tuple[float, float]:
        """(total_in, total_out) bytes over the range — summed per-bucket max-min (reset-safe)."""
        delta = _bucket_delta(bucket)
        clauses = ["tenant_id = :tid", "metric IN ('iface.bytes_in','iface.bytes_out')", "time >= :frm", "time < :to"]
        params: dict = {"bucket": delta, "tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        sql = text(
            "SELECT metric, SUM(d) AS total FROM ("
            "  SELECT time_bucket(:bucket, time) AS b, device_id, label, metric, "
            "         GREATEST(max(value) - min(value), 0) AS d "
            f"  FROM metrics WHERE {where} "
            "  GROUP BY b, device_id, label, metric"
            ") s GROUP BY metric"
        )
        rows = (await self.session.execute(sql, params)).all()
        by_metric = {r.metric: float(r.total) for r in rows}
        return by_metric.get("iface.bytes_in", 0.0), by_metric.get("iface.bytes_out", 0.0)

    async def availability_series(
        self, *, frm: datetime, to: datetime, bucket: str, device_id: uuid.UUID,
    ) -> tuple[list[tuple[datetime, int]], float]:
        """Per-bucket up(1)/down(0) from successful-poll presence (cpu.pct), plus uptime %."""
        delta = _bucket_delta(bucket)
        sql = text(
            "SELECT time_bucket(:bucket, time) AS b, count(*) AS c "
            "FROM metrics WHERE tenant_id = :tid AND device_id = :did AND metric = 'cpu.pct' "
            "AND time >= :frm AND time < :to GROUP BY b"
        )
        rows = (await self.session.execute(
            sql, {"bucket": delta, "tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        )).all()
        present = [r.b for r in rows]  # bucket starts that had a poll
        series: list[tuple[datetime, int]] = []
        cur = frm
        while cur < to:
            up = any(cur <= b < cur + delta for b in present)
            series.append((cur, 1 if up else 0))
            cur = cur + delta
        uptime = (sum(v for _, v in series) / len(series) * 100.0) if series else 0.0
        return series, uptime

    # ── Enrichment accessors (report-enrichment) ─────────────────────────────

    async def health_summary(
        self, *, frm: datetime, to: datetime, bucket: str, device_id: uuid.UUID,
    ) -> HealthSummary:
        """avg + peak of cpu/mem/disk percentage, plus a bucketed cpu sparkline series."""
        delta = _bucket_delta(bucket)
        stat_sql = text(
            "SELECT metric, avg(value) AS a, max(value) AS p FROM metrics "
            "WHERE tenant_id = :tid AND device_id = :did "
            "AND metric IN ('cpu.pct','mem.pct','disk.pct') AND time >= :frm AND time < :to "
            "GROUP BY metric"
        )
        rows = (await self.session.execute(
            stat_sql, {"tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        )).all()
        by_metric = {r.metric: (float(r.a), float(r.p)) for r in rows}

        def _stat(name: str) -> HealthStat:
            v = by_metric.get(name)
            return HealthStat(avg=None, peak=None) if v is None else HealthStat(avg=round(v[0], 1), peak=round(v[1], 1))

        series_sql = text(
            "SELECT time_bucket(:bucket, time) AS b, avg(value) AS a FROM metrics "
            "WHERE tenant_id = :tid AND device_id = :did AND metric = 'cpu.pct' "
            "AND time >= :frm AND time < :to GROUP BY b ORDER BY b"
        )
        series_rows = (await self.session.execute(
            series_sql, {"bucket": delta, "tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        )).all()
        cpu_series = [(r.b, float(r.a)) for r in series_rows]
        return HealthSummary(
            cpu=_stat("cpu.pct"), mem=_stat("mem.pct"), disk=_stat("disk.pct"),
            cpu_series=cpu_series, has_data=bool(by_metric),
        )

    async def gateway_quality(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID,
    ) -> list[GatewayQuality]:
        """Per-gateway (label) avg RTT, avg loss, and availability over the range."""
        sql = text(
            "SELECT label, "
            "  avg(value) FILTER (WHERE metric = 'gateway.rtt_ms') AS rtt, "
            "  avg(value) FILTER (WHERE metric = 'gateway.loss_pct') AS loss, "
            "  avg(value) FILTER (WHERE metric = 'gateway.up') AS up "
            "FROM metrics WHERE tenant_id = :tid AND device_id = :did "
            "AND metric IN ('gateway.rtt_ms','gateway.loss_pct','gateway.up') "
            "AND time >= :frm AND time < :to GROUP BY label ORDER BY label"
        )
        rows = (await self.session.execute(
            sql, {"tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        )).all()
        return [
            GatewayQuality(
                name=r.label or "—",
                rtt_ms=round(float(r.rtt), 1) if r.rtt is not None else None,
                loss_pct=round(float(r.loss), 1) if r.loss is not None else None,
                up_pct=round(float(r.up) * 100.0, 1) if r.up is not None else 0.0,
            )
            for r in rows
        ]

    async def vpn_status(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID,
    ) -> list[VpnStatus]:
        """Per-tunnel (label) availability over the range."""
        sql = text(
            "SELECT label, avg(value) AS up FROM metrics "
            "WHERE tenant_id = :tid AND device_id = :did AND metric = 'vpn.up' "
            "AND time >= :frm AND time < :to GROUP BY label ORDER BY label"
        )
        rows = (await self.session.execute(
            sql, {"tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        )).all()
        return [VpnStatus(name=r.label or "—", up_pct=round(float(r.up) * 100.0, 1)) for r in rows]

    async def alerts_in_range(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID, limit: int = 50,
    ) -> list[AlertRow]:
        """Alerts opened within the range for this device, newest first."""
        sql = text(
            "SELECT type, label, severity, opened_at, resolved_at FROM alerts "
            "WHERE tenant_id = :tid AND device_id = :did AND opened_at >= :frm AND opened_at < :to "
            "ORDER BY opened_at DESC LIMIT :limit"
        )
        rows = (await self.session.execute(
            sql, {"tid": self.tenant_id, "did": device_id, "frm": frm, "to": to, "limit": min(limit, 200)}
        )).all()
        return [
            AlertRow(type=r.type, label=r.label or "", severity=r.severity or "warning",
                     opened_at=r.opened_at, resolved_at=r.resolved_at)
            for r in rows
        ]

    async def config_changes_in_range(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID, limit: int = 50,
    ) -> tuple[int, list[ConfigChangeRow]]:
        """(count, newest-first list) of config changes APPLIED to this device within the range."""
        where = (
            "tenant_id = :tid AND device_id = :did AND status = 'applied' "
            "AND applied_at >= :frm AND applied_at < :to"
        )
        params = {"tid": self.tenant_id, "did": device_id, "frm": frm, "to": to}
        count = (await self.session.execute(
            text(f"SELECT count(*) AS c FROM config_changes WHERE {where}"), params
        )).scalar_one()
        rows = (await self.session.execute(
            text(
                f"SELECT kind, operation, target, applied_at FROM config_changes WHERE {where} "
                "ORDER BY applied_at DESC LIMIT :limit"
            ),
            {**params, "limit": min(limit, 200)},
        )).all()
        items = [
            ConfigChangeRow(kind=r.kind, operation=r.operation, target=r.target or "", applied_at=r.applied_at)
            for r in rows
        ]
        return int(count), items

    async def kpis(self, *, frm: datetime, to: datetime, bucket: str) -> Kpis:
        """Tenant-level KPIs for the executive summary band."""
        delta = _bucket_delta(bucket)
        dev = (await self.session.execute(
            text(
                "SELECT count(*) AS total, count(*) FILTER (WHERE status = 'reachable') AS online "
                "FROM devices WHERE tenant_id = :tid"
            ),
            {"tid": self.tenant_id},
        )).one()
        attacks = (await self.session.execute(
            text(
                "SELECT count(*) AS c FROM events "
                "WHERE tenant_id = :tid AND source = 'ids' AND time >= :frm AND time < :to"
            ),
            {"tid": self.tenant_id, "frm": frm, "to": to},
        )).scalar_one()
        tin, tout = await self.bandwidth_totals(frm=frm, to=to, bucket=bucket, device_id=None)
        alerts = (await self.session.execute(
            text(
                "SELECT count(*) AS c FROM alerts "
                "WHERE tenant_id = :tid AND opened_at >= :frm AND opened_at < :to"
            ),
            {"tid": self.tenant_id, "frm": frm, "to": to},
        )).scalar_one()
        # Fleet-wide availability: share of (device, bucket) cells that recorded a successful poll.
        n_buckets = max(1, int((to - frm) / delta))
        total_devices = int(dev.total)
        present = (await self.session.execute(
            text(
                "SELECT count(*) AS c FROM ("
                "  SELECT device_id, time_bucket(:bucket, time) AS b FROM metrics "
                "  WHERE tenant_id = :tid AND metric = 'cpu.pct' AND time >= :frm AND time < :to "
                "  GROUP BY device_id, b"
                ") s"
            ),
            {"bucket": delta, "tid": self.tenant_id, "frm": frm, "to": to},
        )).scalar_one()
        denom = total_devices * n_buckets
        uptime = round(min(100.0, int(present) / denom * 100.0), 1) if denom else 0.0
        return Kpis(
            devices_total=total_devices,
            devices_online=int(dev.online),
            attacks_blocked=int(attacks),
            data_total=tin + tout,
            uptime_pct=uptime,
            alerts_count=int(alerts),
        )

    async def attacker_countries(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID | None = None,
        limit: int | None = None, geoip: GeoIp,
    ) -> list[CountryCount]:
        """IDS attacker IPs (`events.src_ip`) rolled up by resolved country, with counts + share %.

        GROUP BY src_ip first (collapses volume), then map each distinct IP -> country code via the
        injected `GeoIp` (PRIVATE for internal space, UNKNOWN for unparseable/not-found). Returns rows
        sorted by count desc then code, optionally truncated to the top `limit`. Empty input -> []."""
        clauses = [
            "tenant_id = :tid", "source = 'ids'", "src_ip <> ''", "time >= :frm", "time < :to",
        ]
        params: dict = {"tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        # Bound the per-IP resolution work: serve only the most-frequent distinct IPs (they dominate the
        # country breakdown; the long tail is negligible for a top-N view) so a wide range can't pull an
        # unbounded distinct-IP set into Python.
        params["scan_cap"] = 10000
        sql = text(
            f"SELECT src_ip, count(*) AS c FROM events WHERE {where} "
            "GROUP BY src_ip ORDER BY c DESC LIMIT :scan_cap"
        )
        rows = (await self.session.execute(sql, params)).all()

        by_code: dict[str, int] = {}
        for r in rows:
            code = geoip.country(str(r.src_ip)) or UNKNOWN
            by_code[code] = by_code.get(code, 0) + int(r.c)
        total = sum(by_code.values())
        if total == 0:
            return []
        counts = [
            CountryCount(code=code, count=count, pct=round(count / total * 100, 1))
            for code, count in by_code.items()
        ]
        counts.sort(key=lambda x: (-x.count, x.code))
        if limit is not None:
            counts = counts[:limit]
        return counts

    async def perimeter_top(
        self, *, kind: str, frm: datetime, to: datetime, geoip: GeoIp | None,
        limit: int, device_id: uuid.UUID | None = None,
    ) -> list[PerimeterRow]:
        """Top attacker IPs for a perimeter `kind` active in [frm, to], ranked by cumulative count.

        Reads the bounded `perimeter_attacker` rollup (already per-src_ip per device) and aggregates by
        src_ip ACROSS devices: SUM(count), MAX(last_seen), most-recent detail. `count` is cumulative —
        the rollup is not per-window — so the window filters WHICH attackers (by last_seen), not the
        count. label = last attempted username (login_failed) / most-targeted port (firewall_block).
        Country via the injected GeoIp; empty input -> []."""
        clauses = ["tenant_id = :tid", "kind = :kind", "last_seen >= :frm", "last_seen < :to"]
        params: dict = {"tid": self.tenant_id, "kind": kind, "frm": frm, "to": to, "lim": limit}
        if device_id is not None:
            clauses.append("device_id = :did")
            params["did"] = device_id
        where = " AND ".join(clauses)
        sql = text(
            "SELECT src_ip, sum(count) AS c, max(last_seen) AS seen, "
            "(array_agg(detail ORDER BY last_seen DESC))[1] AS detail "
            f"FROM perimeter_attacker WHERE {where} "
            "GROUP BY src_ip ORDER BY c DESC, src_ip LIMIT :lim"
        )
        rows = (await self.session.execute(sql, params)).all()
        out: list[PerimeterRow] = []
        for r in rows:
            detail = r.detail if isinstance(r.detail, dict) else {}
            out.append(PerimeterRow(
                src_ip=str(r.src_ip),
                country=(geoip.country(str(r.src_ip)) if geoip else None) or UNKNOWN,
                count=int(r.c),
                last_seen=r.seen,
                label=_perimeter_label(kind, detail),
            ))
        return out

    async def reliability_rollup(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID | None = None,
        notable_limit: int = 15,
    ) -> ReliabilityRollup:
        """Roll up `source="service"` (reliability) events in [frm, to] for the tenant/device set:
        counts by category (reboot/service/disk) with share %, plus a recent-events list (newest
        first). Tenant-scoped like the sibling event aggregators; empty range -> empty rollup."""
        # Per-table clause builder so both queries share identical, explicitly-prefixed predicates
        # (the notable-events query joins `devices`, so every column must be qualified to avoid an
        # ambiguous reference). `prefix` is a source-code constant — never request-derived.
        params: dict = {"tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            params["did"] = device_id

        def _where(prefix: str) -> str:
            cl = [
                f"{prefix}tenant_id = :tid", f"{prefix}source = 'service'",
                f"{prefix}time >= :frm", f"{prefix}time < :to",
            ]
            if device_id is not None:
                cl.append(f"{prefix}device_id = :did")
            return " AND ".join(cl)

        cat_rows = (await self.session.execute(
            text(
                f"SELECT category AS c, count(*) AS n FROM events WHERE {_where('')} "
                "GROUP BY category ORDER BY n DESC, category"
            ),
            params,
        )).all()
        total = sum(int(r.n) for r in cat_rows)
        by_category = [
            ReliabilityCount(
                category="" if r.c is None else str(r.c), count=int(r.n),
                pct=round(int(r.n) / total * 100, 1) if total else 0.0,
            )
            for r in cat_rows
        ]

        # Recent notable events, joined to the device name (newest first). The tenant filter + RLS
        # keep it scoped; the join is just for the display name.
        notable_rows = (await self.session.execute(
            text(
                "SELECT e.time AS ts, e.category AS c, e.name AS n, e.severity AS sev, "
                "COALESCE(d.name, '') AS device "
                f"FROM events e LEFT JOIN devices d ON d.id = e.device_id WHERE {_where('e.')} "
                "ORDER BY e.time DESC LIMIT :lim"
            ),
            {**params, "lim": min(notable_limit, 200)},
        )).all()
        notable = [
            ReliabilityEvent(
                time=r.ts, category="" if r.c is None else str(r.c), name=str(r.n),
                severity=str(r.sev) or "medium", device=str(r.device),  # "medium" = raw tier, sev_fn maps it
            )
            for r in notable_rows
        ]
        return ReliabilityRollup(by_category=by_category, notable=notable, total=total)

    async def config_audit_rollup(
        self, *, frm: datetime, to: datetime, device_id: uuid.UUID | None = None,
        notable_limit: int = 15,
    ) -> ConfigAuditRollup:
        """Roll up `source="config_audit"` (box config-change) events in [frm, to] for the tenant/device
        set: counts by channel (api/gui/system, from the `action` column) with share %, the direct/drift
        total (channels gui/system, i.e. `severity='medium'`), plus a recent-changes list (newest first).
        Tenant-scoped like the sibling event aggregators; empty range -> empty rollup."""
        # Per-table clause builder so both queries share identical, explicitly-prefixed predicates
        # (the notable-changes query joins `devices`, so every column must be qualified to avoid an
        # ambiguous reference). `prefix` is a source-code constant — never request-derived.
        params: dict = {"tid": self.tenant_id, "frm": frm, "to": to}
        if device_id is not None:
            params["did"] = device_id

        def _where(prefix: str) -> str:
            cl = [
                f"{prefix}tenant_id = :tid", f"{prefix}source = 'config_audit'",
                f"{prefix}time >= :frm", f"{prefix}time < :to",
            ]
            if device_id is not None:
                cl.append(f"{prefix}device_id = :did")
            return " AND ".join(cl)

        chan_rows = (await self.session.execute(
            text(
                f"SELECT action AS a, count(*) AS n FROM events WHERE {_where('')} "
                "GROUP BY action ORDER BY n DESC, action"
            ),
            params,
        )).all()
        total = sum(int(r.n) for r in chan_rows)
        by_channel = [
            ConfigChannelCount(
                channel="" if r.a is None else str(r.a), count=int(r.n),
                pct=round(int(r.n) / total * 100, 1) if total else 0.0,
            )
            for r in chan_rows
        ]
        # Direct/drift count = the gui/system channels (the on-box, non-API changes).
        direct = sum(c.count for c in by_channel if c.channel in _CONFIG_DRIFT_CHANNELS)

        # Recent notable changes, joined to the device name (newest first). The tenant filter + RLS
        # keep it scoped; the join is just for the display name.
        notable_rows = (await self.session.execute(
            text(
                "SELECT e.time AS ts, e.name AS actor, e.category AS area, e.action AS chan, "
                "COALESCE(d.name, '') AS device "
                f"FROM events e LEFT JOIN devices d ON d.id = e.device_id WHERE {_where('e.')} "
                "ORDER BY e.time DESC LIMIT :lim"
            ),
            {**params, "lim": min(notable_limit, 200)},
        )).all()
        notable = []
        for r in notable_rows:
            chan = "" if r.chan is None else str(r.chan)
            notable.append(ConfigChangeEvent(
                time=r.ts, actor=str(r.actor), area="" if r.area is None else str(r.area),
                channel=chan, direct=(chan in _CONFIG_DRIFT_CHANNELS), device=str(r.device),
            ))
        return ConfigAuditRollup(
            by_channel=by_channel, notable=notable, total=total, direct=direct,
        )
