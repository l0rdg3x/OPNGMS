"""Pure parsers: raw OPNsense JSON -> normalized dicts. No HTTP, no I/O.

Every function tolerates missing/unexpected keys (safe defaults) and never raises on
shape. The shapes were verified against a live OPNsense 26.1.9 (see the connector design
spec). Keeping these pure makes them testable against captured fixtures without HTTP.
"""
import hashlib
import re
from datetime import datetime, timezone


def num(v) -> float:
    """First float in a string like '12.3 ms' / '0.0 %' / '~' / a number; 0.0 if none."""
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
    return float(m.group()) if m else 0.0


def parse_ts(value) -> datetime:
    """Always tz-aware (naive -> UTC; unparsable -> now UTC)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


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
    """wireguard/service/show -> [{name, up}]. Envelope key is `rows` (not `tunnels`)."""
    out = []
    for row in (data or {}).get("rows", []) or []:
        name = row.get("name") or row.get("instance") or row.get("if", "")
        if "connected" in row:
            up = _truthy(row.get("connected"))
        else:
            hs = str(row.get("latest-handshake", "")).strip()
            up = bool(hs) and hs != "0"
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
    """firmware/info -> {product_version, plugins}. Reads the `plugin` array (OPNsense
    plugins) and keeps only installed ones — NOT the much larger `package` array."""
    info = info or {}
    raw = info.get("plugin")
    items = raw if isinstance(raw, list) else []
    plugins = [
        p.get("name", "")
        for p in items
        if isinstance(p, dict)
        and str(p.get("installed", "")) in ("1", "true", "True")
        and p.get("name")
    ]
    return {"product_version": parse_firmware_version(info), "plugins": plugins}
