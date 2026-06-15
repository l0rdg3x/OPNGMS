"""Pure parsers: raw OPNsense JSON -> normalized dicts. No HTTP, no I/O.

Every function tolerates missing/unexpected keys (safe defaults) and never raises on
shape. The shapes were verified against a live OPNsense 26.1.9 (see the connector design
spec). Keeping these pure makes them testable against captured fixtures without HTTP.
"""
import hashlib
import re
from datetime import UTC, datetime


def num(v) -> float:
    """First float in a string like '12.3 ms' / '0.0 %' / '~' / a number; 0.0 if none."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
    return float(m.group()) if m else 0.0


def parse_ts(value) -> datetime:
    """Always tz-aware (naive -> UTC; unparsable -> now UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return datetime.now(UTC)


def event_key(ts: datetime, *parts) -> str:
    """Discriminating hash of event content (used when no stable source id is present)."""
    h = hashlib.sha1("|".join([ts.isoformat(), *[str(p) for p in parts]]).encode())
    return h.hexdigest()


def parse_uptime(s) -> int:
    """'HH:MM:SS' or 'N day(s), HH:MM:SS' -> seconds. 0 on unparsable."""
    s = str(s or "")
    days_match = re.search(r"(\d+)\s+day", s)
    days = int(days_match.group(1)) if days_match else 0
    hms = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", s)
    if not hms:
        return days * 86400
    h, mi, sec = (int(x) for x in hms.groups())
    return days * 86400 + h * 3600 + mi * 60 + sec


def parse_cores(cputype) -> int:
    """['... (2 cores, 4 threads)'] -> 2. Default 1."""
    text = cputype[0] if isinstance(cputype, list) and cputype else str(cputype or "")
    m = re.search(r"(\d+)\s+cores?", str(text))
    return int(m.group(1)) if m else 1


def parse_system_info(resources: dict, disk: dict, time: dict, cputype) -> dict:
    """CPU/mem/disk/uptime from the four diagnostics endpoints. CPU% is loadavg-derived."""
    mem = (resources or {}).get("memory", {}) or {}
    total = num(mem.get("total"))
    used = num(mem.get("used"))
    mem_pct = round(used / total * 100, 1) if total else 0.0

    disk_pct = 0.0
    for d in (disk or {}).get("devices", []) or []:
        if d.get("mountpoint") == "/":
            disk_pct = num(d.get("used_pct"))
            break

    uptime_seconds = parse_uptime((time or {}).get("uptime"))
    load_terms = str((time or {}).get("loadavg", "")).split(",")
    load1m = num(load_terms[0]) if load_terms else 0.0
    cores = parse_cores(cputype)
    cpu_pct = min(100.0, round(load1m / cores * 100, 1)) if cores else 0.0

    return {
        "cpu_pct": cpu_pct,
        "mem_pct": mem_pct,
        "disk_pct": disk_pct,
        "uptime_seconds": uptime_seconds,
    }


def parse_interfaces(traffic: dict) -> list[dict]:
    """diagnostics/traffic/interface -> [{name, up, bytes_in, bytes_out}].

    `link state` is the FreeBSD enum (0=unknown, 1=down, 2=up); only "2" is up.
    """
    interfaces = (traffic or {}).get("interfaces")
    if not isinstance(interfaces, dict):
        return []
    out = []
    for v in interfaces.values():
        out.append({
            "name": v.get("name", ""),
            "up": str(v.get("link state")) == "2",
            "bytes_in": num(v.get("bytes received")),
            "bytes_out": num(v.get("bytes transmitted")),
        })
    return out


def parse_gateways(data: dict) -> list[dict]:
    """routes/gateway/status -> [{name, up, rtt_ms, loss_pct}]. '~'/units handled by num()."""
    out = []
    for g in (data or {}).get("items", []) or []:
        if not isinstance(g, dict):
            continue
        status = str(g.get("status", "")).lower()
        out.append({
            "name": g.get("name", ""),
            "up": status not in ("down", "force_down"),
            "rtt_ms": num(g.get("delay")),
            "loss_pct": num(g.get("loss")),
        })
    return out


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes", "on")


def parse_vpn(data: dict) -> list[dict]:
    """wireguard/service/show -> [{name, up}]. Envelope key is `rows` (not `tunnels`).

    Verified against OPNsense 26.1.9 (live throwaway tunnel): each row carries
    `peer-status` ("online"/"offline") and `latest-handshake-epoch`; there is NO `connected`
    field. `up` means a peer is actually connected, not merely that the interface is
    configured (`status: "up"`).
    """
    out = []
    for row in (data or {}).get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name") or row.get("instance") or row.get("if", "")
        if "connected" in row:                        # legacy / alternate shape
            up = _truthy(row.get("connected"))
        elif "peer-status" in row:                    # real field (26.1.9)
            up = str(row.get("peer-status")).strip().lower() == "online"
        else:                                          # handshake-recency fallback
            hs = str(row.get("latest-handshake-epoch") or row.get("latest-handshake") or "").strip()
            up = bool(hs) and hs not in ("0", "", "None")
        out.append({"name": name, "up": up})
    return out


def parse_firmware_version(data: dict) -> str:
    """Version from top-level `product_version` (firmware/info) or `product.product_version`
    (firmware/status). Empty string if absent."""
    data = data or {}
    v = data.get("product_version")
    if not v and isinstance(data.get("product"), dict):
        v = data["product"].get("product_version")
    return v or ""


def parse_plugins(info: dict) -> dict:
    """firmware/info -> {product_version, plugins, available}.

    `plugins` keeps only INSTALLED plugin names (backward-compatible — the inventory builder relies on
    it). `available` is EVERY plugin the box reports, each as {name, installed(bool), version,
    locked(bool)} — the full install-state list the Plugins UI needs. Reads the `plugin` array
    (OPNsense plugins), NOT the much larger `package` array.
    """
    info = info or {}
    raw = info.get("plugin")
    items = [p for p in (raw if isinstance(raw, list) else []) if isinstance(p, dict) and p.get("name")]
    available = [
        {
            "name": p.get("name", ""),
            "installed": str(p.get("installed", "")) in ("1", "true", "True"),
            "version": p.get("version", ""),
            "locked": _truthy(p.get("locked")),
        }
        for p in items
    ]
    plugins = [a["name"] for a in available if a["installed"]]
    return {"product_version": parse_firmware_version(info), "plugins": plugins, "available": available}


def _rows(data, *keys) -> list:
    """Rows from a dict (first matching key holding a list) OR a bare list (the empty-GET
    edge that used to crash `.get()`). Anything else -> []."""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def parse_ids_rows(data) -> list[dict]:
    """ids/service/queryAlerts (POST) rows -> normalized IDS events (eve.json shape).

    Defensive toward key variants (alert.* nested or flat, dest_ip/dst_ip). event_key is a
    stable source id when present, otherwise a discriminating content hash."""
    out: list[dict] = []
    for r in _rows(data, "rows", "alerts"):
        if not isinstance(r, dict):
            continue
        alert = r.get("alert", {}) if isinstance(r.get("alert"), dict) else {}
        ts = parse_ts(r.get("timestamp"))
        name = alert.get("signature") or r.get("signature") or ""
        src = r.get("src_ip", "")
        dst = r.get("dest_ip", r.get("dst_ip", ""))
        action = alert.get("action", r.get("action", ""))
        severity = str(alert.get("severity", r.get("severity", "")))
        key = r.get("alert_id") or r.get("_id") or event_key(ts, src, dst, name, severity)
        out.append({
            "time": ts, "category": "alert", "src_ip": src, "dst_ip": dst,
            "name": name, "severity": severity, "action": action,
            "event_key": str(key), "attributes": r,
        })
    return out


def parse_dns_rows(data) -> list[dict]:
    """unbound/overview/searchQueries rows -> normalized DNS "visited site" events."""
    out: list[dict] = []
    for r in _rows(data, "rows", "queries"):
        if not isinstance(r, dict):
            continue
        ts = parse_ts(r.get("timestamp", r.get("time")))
        client_ip = r.get("client") or r.get("client_ip") or ""
        domain = r.get("domain") or r.get("query") or r.get("name") or ""
        action = r.get("action", "")
        key = r.get("query_id") or r.get("id") or r.get("_id") or event_key(
            ts, client_ip, domain, action)
        out.append({
            "time": ts, "category": "query", "src_ip": client_ip, "dst_ip": "",
            "name": domain, "severity": "", "action": action,
            "event_key": str(key), "attributes": r,
        })
    return out


def parse_version(s) -> tuple[int, int, int, int]:
    """OPNsense 'YY.M.point[_hotfix]' -> (year, month, point, hotfix); missing parts -> 0.

    The '_hotfix' suffix (e.g. 24.7.1_2) is not PEP 440, so this is parsed by hand. Defensive:
    non-numeric / unexpected input never raises (best-effort, 0-filled)."""
    base, _, hot = str(s or "").strip().partition("_")
    nums = []
    for part in base.split(".")[:3]:
        m = re.match(r"\d+", part)
        nums.append(int(m.group()) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    hm = re.match(r"\d+", hot)
    return (nums[0], nums[1], nums[2], int(hm.group()) if hm else 0)


def series_of(s) -> str:
    """'26.1.9_1' -> '26.1' (the YY.M series; point/hotfix ignored)."""
    y, m, _, _ = parse_version(s)
    return f"{y}.{m}"


def parse_firewall_blocks(data) -> list[dict]:
    """diagnostics/firewall/log rows -> normalized firewall-BLOCK observations (action=block only).

    Structured source; `__digest__` is the per-line dedup key. Defensive toward missing keys; the
    firewall log returns a bare JSON array, which `_rows` already handles."""
    out: list[dict] = []
    for r in _rows(data):
        if not isinstance(r, dict) or str(r.get("action", "")).lower() != "block":
            continue
        src = r.get("src", "")
        if not src:
            continue
        ts = parse_ts(r.get("__timestamp__") or r.get("timestamp"))
        key = r.get("__digest__") or event_key(ts, src, r.get("dst", ""), r.get("dstport", ""))
        out.append({
            "time": ts,
            "src_ip": src,
            "name": str(r.get("dstport", "")),  # the targeted port
            "event_key": str(key),
            "attributes": {k: r.get(k) for k in ("dst", "dstport", "srcport", "interface", "protoname")},
        })
    return out


# Failed-login lines on OPNsense's audit log (process_name="audit") name the attempted user + the
# remote IP. Matches the known failure families ("authentication failed", "could not authenticate",
# "wrong password", "login failed", "denied") followed by `user '<name>' ... from[:] <ip>`. Fail-safe:
# an unrecognized line is skipped (NEVER crashes ingest). Verify/extend against a really-attacked box.
_AUTH_FAIL = re.compile(
    r"(?:authentication failed|could not authenticate|wrong (?:password|username)|login failed|denied)"
    r".*?user '(?P<user>[^']+)'.*?from[: ]+(?P<ip>\d{1,3}(?:\.\d{1,3}){3})",
    re.IGNORECASE,
)


def parse_auth_failures(data) -> list[dict]:
    """diagnostics/log/core/audit rows -> failed-login observations (process_name=audit only).

    Text-log parsing; fail-safe (an unrecognized line is skipped). Extracts the attempted username +
    the source IP."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict) or r.get("process_name") != "audit":
            continue
        m = _AUTH_FAIL.search(str(r.get("line", "")))
        if not m:
            continue
        ts = parse_ts(r.get("timestamp"))
        ip, user = m.group("ip"), m.group("user")
        out.append({
            "time": ts,
            "src_ip": ip,
            "name": user,
            "event_key": event_key(ts, ip, user),
            "attributes": {"username": user, "severity": r.get("severity", "")},
        })
    return out


# Curated, ORDERED classifier for reliability events out of the system log
# (diagnostics/log/core/system). Each rule is (category, name, base_severity, process_predicate,
# line_regex). process_predicate None matches any process. First matching rule wins; an unrecognized
# line is SKIPPED (we store only classified reliability events, NOT the whole system log — that is the
# log-lake's job). These line patterns are a RUNTIME-VERIFY starter set, tuned against real events in
# follow-ups. The kernel-banner boot rule was live-verified on a real OPNsense 26.1.9 box (syslog-ng):
# that box has no `syslogd: kernel boot file` line, but the FreeBSD kernel prints its Copyright banner
# exactly once per boot — the reliable cross-version once-per-boot marker.
_SERVICE_RULES = [
    ("reboot", "reboot", "high", {"shutdown"}, re.compile(r"\breboot\b", re.I)),
    ("reboot", "boot", "medium", {"syslogd"}, re.compile(r"kernel boot file", re.I)),
    ("reboot", "boot", "medium", {"kernel"}, re.compile(r"Copyright .*FreeBSD", re.I)),
    ("service", "service_crashed", "high", None,
        re.compile(r"\bexited on signal\b|\bcore dumped\b|\bterminated abnormally\b", re.I)),
    ("service", "service_restarted", "medium", {"configd.py", "configd"},
        re.compile(r"\brestart(ing|ed)?\b", re.I)),
    ("disk", "filesystem_full", "high", None,
        re.compile(r"no space left on device|filesystem full|out of (?:disk )?space", re.I)),
    ("disk", "disk_error", "high", {"smartd"}, re.compile(r"\b(?:error|fail|offline)\b", re.I)),
    ("disk", "pool_degraded", "high", None,
        re.compile(r"\bDEGRADED\b|\bFAULTED\b|pool .* unavailable", re.I)),
]
# Log severities that escalate a rule's base severity to "high".
_HIGH_LOG_SEV = {"emerg", "alert", "crit", "err", "error"}


def parse_service_events(data) -> list[dict]:
    """system-log rows -> classified reliability events (reboot / service crash-restart / disk-FS).

    Fail-safe: an unrecognized line is skipped (we store only classified reliability events). Severity
    is the rule's base, escalated to "high" when the row's log severity is emerg/alert/crit/err.
    The rule set is RUNTIME-VERIFY (curated starter, tuned against real events on the box)."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict):
            continue
        proc = str(r.get("process_name", ""))
        # Cap the device-supplied line before the regexes + digest run on it: a real syslog line is well
        # under 8 KiB, so 2000 chars is ample and it bounds per-row CPU even on a hostile/compromised box.
        line = str(r.get("line", ""))[:2000]
        log_sev = str(r.get("severity", "")).lower()
        for category, name, base_sev, procs, rx in _SERVICE_RULES:
            if procs is not None and proc not in procs:
                continue
            if not rx.search(line):
                continue
            ts = parse_ts(r.get("timestamp"))
            severity = "high" if log_sev in _HIGH_LOG_SEV else base_sev
            digest = hashlib.sha1(f"{name}|{line}".encode()).hexdigest()[:16]
            out.append({
                "time": ts,
                "category": category,
                "name": name,
                "severity": severity,
                "event_key": event_key(ts, name, digest),
                "attributes": {"process": proc, "message": line[:500], "log_severity": log_sev},
            })
            break  # first matching rule wins; one event per row
    return out


# Config-change audit lines (process_name="audit") record who changed the config and via which request
# path. Grammar (live-verified, real box 192.168.1.82):
#   user (<user>) changed configuration to <backup> in <path> ...      (local/script change, no IP)
#   user <user>@<ip> changed configuration to <backup> in <path> ...   (remote change, carries source IP)
# Fail-safe: a line that doesn't match is skipped (NEVER raises). The channel rules are a RUNTIME-VERIFY
# starter set (grounded on real api + system samples; the gui form is structurally identical with a .php
# page path) — tuned against the box, same posture as the reliability classifier.
_CONFIG_CHANGE = re.compile(
    r"user\s+(?:\((?P<luser>[^)]+)\)|(?P<ruser>[^@\s]+)@(?P<ip>\d{1,3}(?:\.\d{1,3}){3}))"
    r"\s+changed configuration to\s+(?P<backup>\S+)\s+in\s+(?P<path>\S+)",
    re.IGNORECASE,
)
# A trailing /<uuid> on the request path (e.g. .../delTest/<uuid>) -> stripped for a stable change_ref.
_UUID_TAIL = re.compile(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _classify_channel(path: str) -> str:
    """Map the request path that wrote the config to a change CHANNEL (best-effort drift attribution).

    /api/...                                -> "api"    (programmatic: OPNGMS, a WebGUI MVC page, or another API client)
    a script under /usr/local/opnsense/...  -> "system" (console / cron / firmware tooling)
    another .php page (legacy WebGUI form)   -> "gui"    (a human in the WebGUI)
    anything else                            -> "system" (best-effort default for local/script writes)
    """
    if path.startswith("/api/"):
        return "api"
    if "/usr/local/opnsense/" in path or "/usr/local/etc/" in path:
        return "system"
    if path.endswith(".php"):
        return "gui"
    return "system"


def _change_area(path: str) -> str:
    """Coarse config area from the request path. /api/firewall/filter/addRule -> 'firewall';
    /firewall_rules.php -> 'firewall'; a script path -> the script stem. 'system' as a last resort."""
    seg = [s for s in path.strip("/").split("/") if s]
    if seg and seg[0] == "api":
        return seg[1] if len(seg) > 1 else "system"
    base = seg[-1] if seg else ""
    base = base.rsplit(".", 1)[0]          # drop the .php extension
    return base.split("_", 1)[0] or "system"


def parse_config_changes(data) -> list[dict]:
    """audit-log rows -> config-change events with best-effort drift attribution.

    Keeps only process_name="audit" lines matching the "changed configuration" grammar; every other line
    (configd.py noise, failed-login lines, garbage) is skipped (fail-safe, never raises). A DIRECT on-box
    change (channel gui/system) is severity "medium" (drift); an API change is "info"."""
    out: list[dict] = []
    for r in _rows(data, "rows"):
        if not isinstance(r, dict) or r.get("process_name") != "audit":
            continue
        m = _CONFIG_CHANGE.search(str(r.get("line", "")))
        if not m:
            continue
        ts = parse_ts(r.get("timestamp"))
        actor = m.group("luser") or m.group("ruser") or ""
        actor_ip = m.group("ip") or ""
        path = m.group("path") or ""
        channel = _classify_channel(path)
        area = _change_area(path)
        change_ref = _UUID_TAIL.sub("", path)
        backup_file = (m.group("backup") or "").rsplit("/", 1)[-1]
        drift = channel in ("gui", "system")
        out.append({
            "time": ts,
            "category": area,
            "src_ip": actor_ip,
            "name": actor,
            "severity": "medium" if drift else "info",
            "action": channel,
            "event_key": event_key(ts, backup_file),
            "attributes": {
                "actor": actor, "actor_ip": actor_ip, "channel": channel, "area": area,
                "change_ref": change_ref, "backup_file": backup_file,
                "message": str(r.get("line", ""))[:500],
            },
        })
    return out
