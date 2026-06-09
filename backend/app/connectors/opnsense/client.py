import hashlib
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.connectors.opnsense.url_safety import UnsafeUrlError, validate_base_url


class OpnsenseError(Exception):
    """Base class for OPNsense connector errors."""


class AuthError(OpnsenseError):
    """API credentials rejected (401/403)."""


class ReachabilityError(OpnsenseError):
    """Device unreachable (DNS/TLS/connection/timeout)."""


class ApiError(OpnsenseError):
    """HTTP error response (non-auth 4xx/5xx)."""

    def __init__(self, status_code: int, message: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}: {message}")


class ParseError(OpnsenseError):
    """Response not interpretable as JSON."""


class OpnsenseClient:
    """Single HTTP boundary toward an OPNsense device.

    HTTP Basic auth (api_key as username, api_secret as password) over HTTPS.
    NOTE: the exact endpoints are TO BE VERIFIED against a real OPNsense; here we use
    `core/firmware/status` for the connection test + firmware version.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        verify_tls: bool = True,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (api_key, api_secret)
        self._verify = verify_tls
        self._timeout = timeout

    async def _request(self, path: str) -> httpx.Response:
        """SSRF-guarded GET toward the device API; the single guarded HTTP boundary.

        Validates the URL, pins the resolved IP (anti DNS-rebinding), keeps the
        original hostname for the Host header + SNI, and maps transport/status
        errors to the connector exceptions. Returns the raw response; callers
        decide how to interpret the body (JSON vs raw text).
        """
        # SSRF guard: validate the scheme/userinfo/host and resolve+pin the IP.
        try:
            pinned_ip, host, port = validate_base_url(self._base_url)
        except UnsafeUrlError as exc:
            # SANITIZED message: no detail of the unsafe URL.
            raise ReachabilityError("unsafe destination") from exc
        # Connect to the pinned IP (anti DNS-rebinding); the original hostname remains
        # for the Host header and for SNI/TLS cert verification.
        conn_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = conn_host if port is None else f"{conn_host}:{port}"
        base_path = urlsplit(self._base_url).path.rstrip("/")
        url = f"https://{netloc}{base_path}/api/{path.lstrip('/')}"
        try:
            async with httpx.AsyncClient(
                verify=self._verify,
                timeout=self._timeout,
                auth=self._auth,
                follow_redirects=False,
            ) as client:
                resp = await client.get(
                    url, headers={"Host": host}, extensions={"sni_hostname": host}
                )
        except httpx.HTTPError as exc:  # ConnectError/Timeout/TLS/etc.
            raise ReachabilityError("device unreachable") from exc
        if resp.status_code in (401, 403):
            raise AuthError(f"auth failed: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            # Do NOT include the upstream body in the error.
            raise ApiError(resp.status_code)
        return resp

    async def _get(self, path: str) -> dict:
        resp = await self._request(path)
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("response not interpretable") from exc

    async def get_config_backup(self) -> str:
        """Download the raw config.xml as text.

        NOTE: the endpoint `core/backup/download/this` is TO BE VERIFIED against a
        real OPNsense device (the response may be wrapped rather than raw XML).
        """
        resp = await self._request("core/backup/download/this")
        return resp.text

    async def get_firmware_status(self) -> dict:
        return await self._get("core/firmware/status")

    async def get_system_info(self) -> dict:
        """CPU/mem/disk/uptime. NOTE: endpoint+fields TO BE VERIFIED against a real OPNsense."""
        data = await self._get("diagnostics/system/systemInformation")
        return {
            "cpu_pct": float((data.get("cpu") or {}).get("used", 0.0)),
            "mem_pct": float((data.get("memory") or {}).get("used_pct", 0.0)),
            "disk_pct": float((data.get("disk") or {}).get("used_pct", 0.0)),
            "uptime_seconds": int(data.get("uptime_seconds", 0)),
        }

    @staticmethod
    def _num(v) -> float:
        """Extract the first float from a string like '12.3 ms' / '0.0 %' / a number.

        NOTE: the exact format of the delay/loss/bytes fields is TO BE VERIFIED against
        a real OPNsense; the regex is defensive to handle string variants.
        """
        import re

        if isinstance(v, (int, float)):
            return float(v)
        m = re.search(r"[-+]?\d*\.?\d+", str(v or ""))
        return float(m.group()) if m else 0.0

    async def get_interfaces(self) -> list[dict]:
        """Per-network-interface statistics.

        NOTE: the `diagnostics/interface/getInterfaceStatistics` endpoint and the
        bytes_received/bytes_transmitted fields are TO BE VERIFIED against a real OPNsense.
        """
        data = await self._get("diagnostics/interface/getInterfaceStatistics")
        out = []
        for it in data.get("interfaces", []):
            out.append({
                "name": it.get("name", ""),
                "up": it.get("status") == "up",
                "bytes_in": self._num(it.get("bytes_received")),
                "bytes_out": self._num(it.get("bytes_transmitted")),
            })
        return out

    async def get_gateways(self) -> list[dict]:
        """Gateway status (RTT, packet-loss).

        NOTE: the `routes/gateway/status` endpoint, the `items` key, and the
        delay/loss fields (with " ms"/" %" units) are TO BE VERIFIED against a real OPNsense.
        A gateway is down only if status is in {"down", "force_down"}.
        """
        data = await self._get("routes/gateway/status")
        out = []
        for g in data.get("items", []):
            status = str(g.get("status", "")).lower()
            out.append({
                "name": g.get("name", ""),
                "up": status not in ("down", "force_down"),
                "rtt_ms": self._num(g.get("delay")),
                "loss_pct": self._num(g.get("loss")),
            })
        return out

    async def get_vpn_status(self) -> list[dict]:
        """WireGuard tunnel status.

        NOTE: the `wireguard/service/show` endpoint and the `tunnels` key with the
        `connected` field are TO BE VERIFIED against a real OPNsense. OpenVPN uses a
        different endpoint (not yet implemented).
        """
        data = await self._get("wireguard/service/show")
        return [
            {"name": t.get("name", ""), "up": bool(t.get("connected"))}
            for t in data.get("tunnels", [])
        ]

    async def get_ids_alerts(self, since: datetime | None = None) -> list[dict]:
        """Normalized Suricata IDS/IPS alerts.

        NOTE: the `ids/service/queryAlerts` endpoint and the payload format are TO BE
        VERIFIED against a real OPNsense. Defensive toward key variants. `since` is a hint:
        the fine filtering and the deduplication happen downstream (cursor + ON CONFLICT).
        """
        data = await self._get("ids/service/queryAlerts")
        out: list[dict] = []
        for r in data.get("rows", data.get("alerts", [])):
            alert = r.get("alert", {}) if isinstance(r.get("alert"), dict) else {}
            ts = self._parse_ts(r.get("timestamp"))
            name = alert.get("signature") or r.get("signature") or ""
            src = r.get("src_ip", "")
            dst = r.get("dest_ip", r.get("dst_ip", ""))
            action = alert.get("action", r.get("action", ""))
            severity = str(alert.get("severity", r.get("severity", "")))
            # DISCRIMINATING event_key: stable source id if present,
            # OTHERWISE a hash of the content (ts+src+dst+signature+severity) so as
            # NOT to collapse distinct events that share the same signature.
            key = r.get("alert_id") or r.get("_id") or self._event_key(
                ts, src, dst, name, severity
            )
            out.append({
                "time": ts,
                "category": "alert",
                "src_ip": src,
                "dst_ip": dst,
                "name": name,
                "severity": severity,
                "action": action,
                "event_key": str(key),
                "attributes": r,
            })
        return out

    async def get_dns_events(self, since: datetime | None = None) -> list[dict]:
        """Normalized DNS queries (Unbound) → "visited sites".

        NOTE: the `unbound/diagnostics/queries` endpoint and the payload format are TO BE
        VERIFIED against a real OPNsense — it is the most uncertain source (see debt 3A).
        Defensive toward key variants. `since` is a hint: fine filtering and dedup happen
        downstream.
        """
        data = await self._get("unbound/diagnostics/queries")
        out: list[dict] = []
        for r in data.get("rows", data.get("queries", [])):
            ts = self._parse_ts(r.get("timestamp", r.get("time")))
            client_ip = r.get("client") or r.get("client_ip") or ""
            domain = r.get("domain") or r.get("query") or r.get("name") or ""
            action = r.get("action", "")  # allowed | blocked
            # discriminating event_key: stable id if present, otherwise a hash of the content.
            key = r.get("query_id") or r.get("id") or r.get("_id") or self._event_key(
                ts, client_ip, domain, action
            )
            out.append({
                "time": ts,
                "category": "query",
                "src_ip": client_ip,
                "dst_ip": "",
                "name": domain,
                "severity": "",
                "action": action,
                "event_key": str(key),
                "attributes": r,
            })
        return out

    @staticmethod
    def _parse_ts(value) -> datetime:
        """Always returns a tz-aware datetime (naive -> UTC; unparsable -> now UTC)."""
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    @staticmethod
    def _event_key(ts, *parts) -> str:
        """Discriminating hash of the event content (no source id available)."""
        h = hashlib.sha1("|".join([ts.isoformat(), *[str(p) for p in parts]]).encode())
        return h.hexdigest()

    async def test_connection(self) -> str | None:
        """Verify reachability+credentials; returns the firmware version or None.

        Raises AuthError/ReachabilityError/ApiError/ParseError on problems.
        """
        data = await self.get_firmware_status()
        # Field TO BE VERIFIED against a real OPNsense (the exact name may differ).
        version = data.get("product_version")
        if version is None and isinstance(data.get("product"), dict):
            version = data["product"].get("product_version")
        return version
