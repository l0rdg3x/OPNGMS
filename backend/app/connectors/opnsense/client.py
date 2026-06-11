import re
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


# firewall/alias/reconfigure reloads the firewall tables and is slow; give it room.
RECONFIGURE_TIMEOUT = 120.0

# Plugin names must be safe for URL path embedding: alphanumeric, dots, hyphens, underscores only.
_PLUGIN_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")

# IDS ruleset filenames embed in the toggleRuleset URL path: restrict to the safe charset
# (verified: all real-box ruleset filenames match this) to prevent path injection.
_RULESET_NAME_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")


def _unflatten(flat: dict) -> dict:
    """{'a.b': 1, 'a.c': 2, 'x': 3} -> {'a': {'b': 1, 'c': 2}, 'x': 3}."""
    out: dict = {}
    for key, val in flat.items():
        parts = key.split(".")
        node = out
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = val
    return out


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
        self, path: str, method: str = "GET", json: dict | None = None, timeout: float | None = None
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
            except (TimeoutError, ssl.SSLError, OSError) as exc:
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
                timeout=timeout or self._timeout,
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

    async def _post(self, path: str, json: dict, timeout: float | None = None) -> dict:
        return self._decode(await self._request(path, "POST", json, timeout=timeout))

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

        Verified against OPNsense 26.1.9: add -> addItem; set/delete need the uuid in the path,
        resolved by exact name via searchItem; reconfigure is slow (long timeout). Goes through
        the single SSRF-guarded HTTP boundary.
        """
        if dry_run:
            return {"dry_run": True, "operation": operation, "target": payload.get("name", "")}
        if operation == "add":
            res = await self._post(
                "firewall/alias/addItem", {"alias": self._normalize_alias_payload(payload)})
        elif operation in ("set", "delete"):
            alias_uuid = await self._resolve_alias_uuid(payload.get("name", ""))
            if operation == "set":
                res = await self._post(
                    f"firewall/alias/setItem/{alias_uuid}",
                    {"alias": self._normalize_alias_payload(payload)})
            else:
                res = await self._post(f"firewall/alias/delItem/{alias_uuid}", {})
        else:
            raise ApiError(0, f"unknown alias operation: {operation}")
        await self._post("firewall/alias/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}

    async def get_setting(self, get_path: str) -> dict:
        """Read an OPNsense model-setting endpoint (for introspection)."""
        return await self._get(get_path)

    async def apply_setting(self, set_path: str, reconfigure_path: str, model_root: str,
                            payload: dict, *, dry_run: bool = True) -> dict:
        """Apply a PARTIAL setting: POST only the templated fields under the model root, then
        reconfigure. Verified: OPNsense `set` merges a partial payload (no clobber). Payload keys are
        dotted paths (e.g. 'general.homenet'); values are strings (option fields = comma-joined keys)."""
        if dry_run:
            return {"dry_run": True, "endpoint": set_path, "fields": sorted(payload.keys())}
        nested = _unflatten(payload)
        res = await self._post(set_path, {model_root: nested})
        await self._post(reconfigure_path, {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}

    async def list_ids_rulesets(self) -> list[dict]:
        """Catalog of installed Suricata/IDS rulesets: [{filename, description, enabled, ...}]."""
        return (await self._get("ids/settings/listRulesets")).get("rows", [])

    async def apply_ids_rulesets(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Enable the listed IDS rulesets (additive/non-destructive), then reload the engine.

        Verified against OPNsense 26.1.9: POST ids/settings/toggleRuleset/{filename}/1 enables one
        ruleset; ids/service/reconfigure reloads Suricata. Each filename is charset-validated
        (anti path-injection) before it is embedded in the URL path. dry_run performs NO mutation."""
        rulesets = list(payload.get("rulesets", []))
        if dry_run:
            return {"dry_run": True, "rulesets": rulesets}
        for name in rulesets:
            await self._post(f"ids/settings/toggleRuleset/{self._ruleset_name(name)}/1", {})
        await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "enabled": rulesets}

    @staticmethod
    def _ruleset_name(name: str) -> str:
        if not name or not _RULESET_NAME_RE.match(name):
            raise ApiError(0, f"invalid ruleset filename: {name!r}")
        return name

    @staticmethod
    def _normalize_alias_payload(payload: dict) -> dict:
        """OPNsense's alias API wants ``content`` as a newline-separated string; a JSON list is
        coerced to the literal 'Array'. Join list/tuple content into the expected string."""
        content = payload.get("content")
        if isinstance(content, (list, tuple)):
            return {**payload, "content": "\n".join(str(c) for c in content)}
        return payload

    async def _resolve_alias_uuid(self, name: str) -> str:
        """Resolve a firewall alias name to its uuid via searchItem (EXACT name match).

        searchItem does substring matching, so we filter to an exact name. Refuses (ApiError)
        when the name is empty or does not resolve to exactly one alias — never mutates on doubt.
        """
        if not name:
            raise ApiError(0, "alias name required for set/delete")
        data = await self._post(
            "firewall/alias/searchItem", {"current": 1, "rowCount": 1000, "searchPhrase": name}
        )
        matches = [r for r in data.get("rows", []) if r.get("name") == name]
        if len(matches) != 1:
            raise ApiError(0, f"alias '{name}' not uniquely resolvable ({len(matches)} exact matches)")
        return matches[0]["uuid"]

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

    async def firmware_check(self) -> dict:
        """Trigger a firmware mirror check."""
        return await self._post("core/firmware/check", {}, timeout=RECONFIGURE_TIMEOUT)

    async def firmware_status_raw(self) -> dict:
        """Raw core/firmware/status (updates count, download size, reboot-needed, latest major)."""
        return await self._get("core/firmware/status")

    async def firmware_update(self) -> dict:
        """Apply all available package updates (may reboot)."""
        return await self._post("core/firmware/update", {}, timeout=RECONFIGURE_TIMEOUT)

    async def firmware_upgrade(self) -> dict:
        """Major release upgrade (always reboots)."""
        return await self._post("core/firmware/upgrade", {}, timeout=RECONFIGURE_TIMEOUT)

    async def firmware_upgrade_status(self) -> dict:
        """Progress of a running firmware operation: {status, log}."""
        return await self._get("core/firmware/upgradestatus")

    async def plugin_install(self, name: str) -> dict:
        """Install a plugin by exact name (charset-validated to avoid path injection)."""
        return await self._post(f"core/firmware/install/{self._plugin_name(name)}", {}, timeout=RECONFIGURE_TIMEOUT)

    async def plugin_remove(self, name: str) -> dict:
        """Remove a plugin by exact name (charset-validated)."""
        return await self._post(f"core/firmware/remove/{self._plugin_name(name)}", {}, timeout=RECONFIGURE_TIMEOUT)

    @staticmethod
    def _plugin_name(name: str) -> str:
        if not name or not _PLUGIN_NAME_RE.match(name):
            raise ApiError(0, f"invalid plugin name: {name!r}")
        return name

    async def get_firmware_status(self) -> dict:
        return {"product_version": await self._capability("firmware_status")}

    async def test_connection(self) -> str | None:
        """Verify reachability+credentials; return the firmware version or None.

        Raises AuthError/ReachabilityError/ApiError/ParseError on problems.
        """
        return (await self._capability("firmware_status")) or None
