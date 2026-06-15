"""Declarative (edition, version) capability matrix for the OPNsense connector.

Each capability maps to an ordered list of ProfileRule; the resolver returns the EndpointSpec
of the first rule whose (edition, version-range) matches a device. The LAST rule of every
capability MUST be the unconstrained default (edition="any", no bounds).
"""
from collections.abc import Callable
from dataclasses import dataclass

from app.connectors.opnsense import parsers

# Rows requested from the paged IDS/DNS query endpoints (dedup happens downstream).
MAX_QUERY_ROWS = 500


@dataclass(frozen=True)
class Request:
    method: str             # "GET" | "POST"
    path: str               # e.g. "diagnostics/traffic/interface" (may include a ?query)
    body: dict | None = None
    kind: str = "json"      # "json" | "text"


@dataclass(frozen=True)
class EndpointSpec:
    requests: tuple          # tuple[Request, ...] — 1 for most capabilities; 4 for system_info
    combine: Callable        # combine(list_of_decoded_responses) -> normalized result


@dataclass(frozen=True)
class ProfileRule:
    edition: str             # "community" | "business" | "devel" | "any"
    min_version: tuple | None  # inclusive lower bound (parse_version tuple) or None
    max_version: tuple | None  # EXCLUSIVE upper bound or None
    spec: EndpointSpec


def _GET(path: str, kind: str = "json") -> Request:
    return Request("GET", path, None, kind)


def _POST(path: str, body: dict) -> Request:
    return Request("POST", path, body, "json")


def _spec(*requests: Request, combine: Callable) -> EndpointSpec:
    return EndpointSpec(requests=tuple(requests), combine=combine)


def _default(spec: EndpointSpec) -> ProfileRule:
    return ProfileRule("any", None, None, spec)


CAPABILITIES: dict[str, list[ProfileRule]] = {
    "system_info": [_default(_spec(
        _GET("diagnostics/system/systemResources"),
        _GET("diagnostics/system/systemDisk"),
        _GET("diagnostics/system/systemTime"),
        _GET("diagnostics/cpu_usage/getCPUType"),
        combine=lambda r: parsers.parse_system_info(r[0], r[1], r[2], r[3])))],
    "interfaces": [_default(_spec(
        _GET("diagnostics/traffic/interface"),
        combine=lambda r: parsers.parse_interfaces(r[0])))],
    "gateways": [_default(_spec(
        _GET("routes/gateway/status"),
        combine=lambda r: parsers.parse_gateways(r[0])))],
    "vpn_status": [_default(_spec(
        _GET("wireguard/service/show"),
        combine=lambda r: parsers.parse_vpn(r[0])))],
    "ids_alerts": [_default(_spec(
        _POST("ids/service/queryAlerts",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_ids_rows(r[0])))],
    "dns_events": [
        # Legacy pre-rename endpoint for old series. The (20,1) boundary is best-effort
        # (no pre-rename hardware available); it documents and exercises the matrix.
        ProfileRule("any", None, (20, 1, 0, 0), _spec(
            _GET("unbound/diagnostics/queries"),
            combine=lambda r: parsers.parse_dns_rows(r[0]))),
        _default(_spec(
            _GET(f"unbound/overview/searchQueries?current=1&rowCount={MAX_QUERY_ROWS}"),
            combine=lambda r: parsers.parse_dns_rows(r[0]))),
    ],
    "firmware_status": [_default(_spec(
        _GET("core/firmware/status"),
        combine=lambda r: parsers.parse_firmware_version(r[0])))],
    "plugin_info": [_default(_spec(
        _GET("core/firmware/info"),
        combine=lambda r: parsers.parse_plugins(r[0])))],
    "config_backup": [_default(_spec(
        _GET("core/backup/download/this", kind="text"),
        combine=lambda r: r[0]))],
    # Perimeter signals. The firewall log is structured (action=block -> attacker src). The audit log
    # holds auth events; OPNsense's diagnostics-log API is POST (a GET returns []), paged like IDS.
    "firewall_blocks": [_default(_spec(
        _GET("diagnostics/firewall/log"),
        combine=lambda r: parsers.parse_firewall_blocks(r[0])))],
    "auth_failures": [_default(_spec(
        _POST("diagnostics/log/core/audit",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_auth_failures(r[0])))],
    # Reliability signals (reboot / service crash-restart / disk-FS) classified out of the system log.
    # Same POST/paged shape as the audit log; the classifier stores only recognized lines.
    "service_events": [_default(_spec(
        _POST("diagnostics/log/core/system",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_service_events(r[0])))],
    # Config-change audit (who/what/when changed the box config, channel-attributed). Same audit-log
    # endpoint as `auth_failures`; the parser keeps the "changed configuration" line family.
    "config_changes": [_default(_spec(
        _POST("diagnostics/log/core/audit",
              {"current": 1, "rowCount": MAX_QUERY_ROWS, "searchPhrase": ""}),
        combine=lambda r: parsers.parse_config_changes(r[0])))],
}
