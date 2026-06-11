import asyncio
import ssl
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.connectors.opnsense.identity import DeviceIdentity, parse_identity
from app.connectors.opnsense.resolver import CapabilityResolver
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
    The read/telemetry endpoints are verified against a real OPNsense 26.1.9 and the raw
    JSON is normalized by the pure functions in ``parsers``. The write path (``apply_alias``)
    and ``get_config_backup`` remain unverified against hardware (out of scope, see the
    connector design spec).
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
        edition: str = "",
        version: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (api_key, api_secret)
        self._verify = verify_tls
        self._fingerprint = tls_fingerprint
        self._timeout = timeout
        self._resolver = CapabilityResolver(edition, version)

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
        return self._decode(await self._request(path))

    async def _post(self, path: str, json: dict) -> dict:
        return self._decode(await self._request(path, "POST", json))

    def set_identity(self, edition: str, version: str) -> None:
        """Switch the resolver to a device's detected (edition, version)."""
        self._resolver = CapabilityResolver(edition, version)

    async def get_device_identity(self) -> DeviceIdentity:
        """Detect edition/version/series from core/firmware/status."""
        return parse_identity(await self._get("core/firmware/status"))

    def _decode(self, resp):
        try:
            return resp.json()
        except ValueError as exc:
            raise ParseError("response not interpretable") from exc

    async def _capability(self, name: str):
        """Resolve a capability to its EndpointSpec, issue its request(s), and combine."""
        spec = self._resolver.resolve(name)
        responses = []
        for req in spec.requests:
            resp = await self._request(req.path, req.method, req.body)
            responses.append(resp.text if req.kind == "text" else self._decode(resp))
        return spec.combine(responses)

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

    async def get_system_info(self) -> dict:
        return await self._capability("system_info")

    async def get_interfaces(self) -> list[dict]:
        return await self._capability("interfaces")

    async def get_gateways(self) -> list[dict]:
        return await self._capability("gateways")

    async def get_vpn_status(self) -> list[dict]:
        return await self._capability("vpn_status")

    async def get_ids_alerts(self, since: datetime | None = None) -> list[dict]:
        """`since` is accepted for caller convenience; filtering/dedup happen downstream."""
        return await self._capability("ids_alerts")

    async def get_dns_events(self, since: datetime | None = None) -> list[dict]:
        """`since` is accepted for caller convenience; filtering/dedup happen downstream."""
        return await self._capability("dns_events")

    async def get_plugin_info(self) -> dict:
        return await self._capability("plugin_info")

    async def get_config_backup(self) -> str:
        return await self._capability("config_backup")

    async def get_firmware_status(self) -> dict:
        return {"product_version": await self._capability("firmware_status")}

    async def test_connection(self) -> str | None:
        """Verify reachability+credentials; return the firmware version or None.

        Raises AuthError/ReachabilityError/ApiError/ParseError on problems.
        """
        return (await self._capability("firmware_status")) or None
