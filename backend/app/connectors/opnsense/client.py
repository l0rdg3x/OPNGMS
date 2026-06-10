import asyncio
import hashlib
import ssl
from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from app.connectors.opnsense import parsers
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
        tls_fingerprint: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (api_key, api_secret)
        self._verify = verify_tls
        self._fingerprint = tls_fingerprint
        self._timeout = timeout

    async def _request(
        self, path: str, method: str = "GET", json: dict | None = None
    ) -> httpx.Response:
        """SSRF-guarded request toward the device API; the single guarded HTTP boundary.

        Validates the URL, pins the resolved IP (anti DNS-rebinding), keeps the
        original hostname for the Host header + SNI, and maps transport/status
        errors to the connector exceptions. Returns the raw response; callers
        decide how to interpret the body (JSON vs raw text). Defaults to GET;
        callers may pass method="POST" with a JSON body for mutations.
        """
        # SSRF guard: validate the scheme/userinfo/host and resolve+pin the IP.
        try:
            pinned_ip, host, port = validate_base_url(self._base_url)
        except UnsafeUrlError as exc:
            # SANITIZED message: no detail of the unsafe URL.
            raise ReachabilityError("unsafe destination") from exc
        # TLS pinning (opt-in): when not doing CA verification but a fingerprint is pinned, verify the
        # device cert BEFORE sending credentials. No fingerprint => permissive (self-signed) as before.
        if not self._verify and self._fingerprint:
            from app.connectors.opnsense.tls_pinning import PinMismatchError, verify_pinned
            try:
                await verify_pinned(host, pinned_ip, port or 443, self._fingerprint, timeout=self._timeout)
            except PinMismatchError as exc:
                raise ReachabilityError("certificate fingerprint mismatch") from exc
            except (ssl.SSLError, OSError, asyncio.TimeoutError) as exc:
                raise ReachabilityError("device unreachable") from exc
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
                resp = await client.request(
                    method,
                    url,
                    headers={"Host": host},
                    extensions={"sni_hostname": host},
                    json=json,
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

    async def _post(self, path: str, json: dict) -> dict:
        resp = await self._request(path, "POST", json)
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("response not interpretable") from exc

    async def apply_alias(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Apply a firewall alias change. dry_run=True (default) performs NO mutation.

        NOTE: endpoints `firewall/alias/{addItem,setItem,delItem}` + `firewall/alias/reconfigure`
        and the payload shape are TO BE VERIFIED against a real OPNsense device (4D-b). Goes
        through the single SSRF-guarded HTTP boundary.
        """
        if dry_run:
            return {"dry_run": True, "operation": operation, "target": payload.get("name", "")}
        endpoints = {
            "add": "firewall/alias/addItem",
            "set": "firewall/alias/setItem",
            "delete": "firewall/alias/delItem",
        }
        if operation not in endpoints:
            raise ApiError(0, f"unknown alias operation: {operation}")
        res = await self._post(endpoints[operation], {"alias": payload})
        await self._post("firewall/alias/reconfigure", {})
        return {"dry_run": False, "result": res}

    async def get_config_backup(self) -> str:
        """Download the raw config.xml as text.

        NOTE: the endpoint `core/backup/download/this` is TO BE VERIFIED against a
        real OPNsense device (the response may be wrapped rather than raw XML).
        """
        resp = await self._request("core/backup/download/this")
        return resp.text

    async def get_firmware_status(self) -> dict:
        """Connection test + firmware version. Normalizes the version to the top level so
        callers (monitoring) read `.get("product_version")` regardless of the raw nesting."""
        data = await self._get("core/firmware/status")
        return {"product_version": parsers.parse_firmware_version(data)}

    async def get_plugin_info(self) -> dict:
        """Installed plugins + product version, for capability discovery."""
        data = await self._get("core/firmware/info")
        return parsers.parse_plugins(data)

    async def get_system_info(self) -> dict:
        """CPU/mem/disk/uptime, aggregated from four diagnostics endpoints (26.1.9)."""
        resources = await self._get("diagnostics/system/systemResources")
        disk = await self._get("diagnostics/system/systemDisk")
        time = await self._get("diagnostics/system/systemTime")
        cputype = await self._get("diagnostics/cpu_usage/getCPUType")
        return parsers.parse_system_info(resources, disk, time, cputype)

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
        """Per-interface bytes + up flag (diagnostics/traffic/interface)."""
        data = await self._get("diagnostics/traffic/interface")
        return parsers.parse_interfaces(data)

    async def get_gateways(self) -> list[dict]:
        """Gateway RTT / packet-loss / up (routes/gateway/status)."""
        data = await self._get("routes/gateway/status")
        return parsers.parse_gateways(data)

    async def get_vpn_status(self) -> list[dict]:
        """WireGuard tunnel/peer status (wireguard/service/show; envelope key `rows`)."""
        data = await self._get("wireguard/service/show")
        return parsers.parse_vpn(data)

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
        """Verify reachability+credentials; return the firmware version or None.

        Raises AuthError/ReachabilityError/ApiError/ParseError on problems.
        """
        data = await self.get_firmware_status()
        return data.get("product_version") or None
