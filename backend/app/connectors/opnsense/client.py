import re
import ssl
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from app.connectors.opnsense.identity import DeviceIdentity, parse_identity
from app.connectors.opnsense.resolver import CapabilityResolver
from app.connectors.opnsense.url_safety import UnsafeUrlError, validate_base_url
from app.core.config import get_settings


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

# OPNsense uuids (RFC4122 + the box's own ids) embed directly in del* URL paths; restrict to
# the safe charset to block path traversal before building the path.
_OPN_UUID_RE = re.compile(r"\A[A-Za-z0-9._-]+\Z")

# An embedded endpoint path (e.g. "unbound/settings/addHostOverride") — MVC API paths are
# slash-separated alphanumerics; reject anything that could escape the /api/ prefix (.., //, etc.).
_OPN_PATH_RE = re.compile(r"\A[A-Za-z0-9_]+(?:/[A-Za-z0-9_]+)+\Z")


def _safe_uuid(value: str) -> str:
    if not value or not _OPN_UUID_RE.match(value):
        raise ValueError(f"unsafe OPNsense uuid: {value!r}")
    return value


def _raise_on_failed(res, what: str) -> None:
    """Raise when an OPNsense MVC mutation was REJECTED. add/set return HTTP 200 with a failure body —
    usually ``{"result": "failed", "validations": {...}}``, but some controllers populate ``validations``
    without setting ``result`` to ``failed`` — so any non-empty ``validations`` on a non-``saved`` result
    is a rejection. A clean save returns ``{"result": "saved"}``; a delete returns ``{"result": "deleted"}``
    (both pass). Surfacing the rejection as an ApiError makes the apply pipeline record the change as
    ``failed`` instead of silently ``applied``. (Verified on a real box: an addPolicy with a bad content
    filter returned a failure body yet was reported OK.)"""
    if not isinstance(res, dict):
        return
    if res.get("result") == "failed" or (res.get("validations") and res.get("result") != "saved"):
        raise ApiError(0, f"{what} rejected by OPNsense: {res.get('validations') or res}")


def _safe_endpoint(path: str) -> str:
    if not path or not _OPN_PATH_RE.match(path):
        raise ApiError(0, f"unsafe catalog endpoint: {path!r}")
    return path


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
        timeout: float | None = None,
        edition: str = "",
        version: str = "",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = (api_key, api_secret)
        self._verify = verify_tls
        self._fingerprint = tls_fingerprint
        # Default per-request timeout comes from OPNSENSE_HTTP_TIMEOUT (.env); an explicit arg overrides.
        self._timeout = timeout if timeout is not None else get_settings().opnsense_http_timeout
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
        _raise_on_failed(res, "firewall alias")
        await self._post("firewall/alias/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}

    async def get_setting(self, get_path: str) -> dict:
        """Read an OPNsense model-setting endpoint (for introspection)."""
        # Symmetry with apply_setting: charset-validate the path before embedding it in the URL
        # (the catalog editor sources it from stored payload — defence-in-depth vs a tampered catalog).
        return await self._get(_safe_endpoint(get_path))

    async def apply_setting(self, set_path: str, reconfigure_path: str, model_root: str,
                            payload: dict, *, dry_run: bool = True, reconfigure: bool = True) -> dict:
        """Apply a PARTIAL setting: POST only the templated fields under the model root, then
        reconfigure. Verified: OPNsense `set` merges a partial payload (no clobber). Payload keys are
        dotted paths (e.g. 'general.homenet'); values are strings (option fields = comma-joined keys).
        `reconfigure=False` skips the reload (the catalog applier batches one reconfigure at the end)."""
        if dry_run:
            return {"dry_run": True, "endpoint": set_path, "fields": sorted(payload.keys())}
        # Defence-in-depth: the catalog_setting kind sources these paths from stored payload, so
        # charset-validate BOTH up front (before any mutation) before embedding them in a URL.
        _safe_endpoint(set_path)
        if reconfigure:
            _safe_endpoint(reconfigure_path)
        nested = _unflatten(payload)
        res = await self._post(set_path, {model_root: nested})
        _raise_on_failed(res, "setting")
        if reconfigure:
            await self._post(reconfigure_path, {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "result": res}

    async def reconfigure(self, reconfigure_path: str) -> dict:
        """Run a model's reconfigure/reload endpoint once (slow; long timeout)."""
        return await self._post(_safe_endpoint(reconfigure_path), {}, timeout=RECONFIGURE_TIMEOUT)

    async def apply_grid_item(self, op: str, endpoints: dict, *, row: str,
                              uuid: str | None = None, item: dict | None = None,
                              dry_run: bool = True) -> dict:
        """Apply ONE ArrayField grid op (add/set/del) under a catalog model's grid endpoints.

        add  -> POST endpoints['add']            {row: item}
        set  -> POST endpoints['set']/{uuid}     {row: item}
        del  -> POST endpoints['del']/{uuid}
        Embedded paths + uuid are charset-validated (anti path-injection). No reconfigure here —
        the catalog applier batches a single reconfigure after all ops. dry_run performs NO mutation."""
        if dry_run:
            return {"dry_run": True, "op": op, "row": row, "uuid": uuid}
        if op == "add":
            path = _safe_endpoint(endpoints["add"])
            res = await self._post(path, {row: item or {}})
        elif op in ("set", "del"):
            # uuid comes from the editor (user input) -> validate to an ApiError (which the apply
            # pipeline handles), not the bare ValueError that _safe_uuid raises for trusted callers.
            if not uuid or not _OPN_UUID_RE.match(uuid):
                raise ApiError(0, f"unsafe grid uuid: {uuid!r}")
            path = _safe_endpoint(endpoints[op])
            body = {row: item or {}} if op == "set" else {}
            res = await self._post(f"{path}/{uuid}", body)
        else:
            raise ApiError(0, f"unknown grid op: {op!r}")
        _raise_on_failed(res, f"grid {op}")
        return {"dry_run": False, "op": op, "result": res}

    async def get_firewall_rule_model(self) -> dict:
        """Blank Rules[new] filter-rule model (option-objects/strings) for the introspection form."""
        return (await self._get("firewall/filter/getRule")).get("rule", {})

    async def apply_firewall_rule(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Upsert (or delete) a Rules[new] filter rule by (description, interface), then apply.

        Verified against OPNsense 26.1.9: firewall/filter addRule/setRule/{uuid}/apply. Identity is
        (description, interface): exactly one match -> setRule; none -> addRule; many -> refuse
        (never mutate on doubt). `operation == "delete"` resolves the same identity and POSTs
        firewall/filter/delRule/{uuid} (for the revert path); an absent rule is a clean no-op.
        dry_run performs NO mutation."""
        description = str(payload.get("description", ""))
        interface = str(payload.get("interface", ""))
        if operation == "delete":
            if dry_run:
                return {"dry_run": True, "operation": "delete", "description": description}
            uuid_ = await self._resolve_rule_uuid(description, interface)
            if uuid_ is None:
                return {"dry_run": False, "operation": "delete", "result": "absent"}
            # uuid_ is box-sourced from searchRule (charset is RFC4122); embed it directly as the
            # existing setRule path does.
            res = await self._post(f"firewall/filter/delRule/{uuid_}", {})
            await self._post("firewall/filter/apply", {}, timeout=RECONFIGURE_TIMEOUT)
            return {"dry_run": False, "operation": "delete", "result": res}
        if dry_run:
            return {"dry_run": True, "description": description, "interface": interface}
        uuid_ = await self._resolve_rule_uuid(description, interface)
        if uuid_ is None:
            res = await self._post("firewall/filter/addRule", {"rule": payload})
            op = "add"
        else:
            res = await self._post(f"firewall/filter/setRule/{uuid_}", {"rule": payload})
            op = "set"
        _raise_on_failed(res, "firewall rule")
        await self._post("firewall/filter/apply", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": op, "result": res}

    async def _resolve_rule_uuid(self, description: str, interface: str) -> str | None:
        """Resolve an automation rule by EXACT (description, interface). None if absent; ApiError if many."""
        if not description:
            raise ApiError(0, "rule description required (it is the rule identity)")
        data = await self._post(
            "firewall/filter/searchRule", {"current": 1, "rowCount": 1000, "searchPhrase": description})
        matches = [r for r in data.get("rows", [])
                   if r.get("description") == description and str(r.get("interface", "")) == interface]
        if len(matches) > 1:
            raise ApiError(0, f"rule '{description}' on '{interface}' not uniquely resolvable ({len(matches)})")
        if not matches:
            return None
        uuid_ = str(matches[0].get("uuid", ""))
        # Box-sourced, but guard before it is embedded in a setRule/delRule URL path (catchable ApiError).
        if not _OPN_UUID_RE.match(uuid_):
            raise ApiError(0, f"rule '{description}' resolved to an unsafe uuid")
        return uuid_

    async def get_monit_test_model(self) -> dict:
        """Blank Monit test model (option-objects/strings) for the introspection form."""
        return (await self._get("monit/settings/getTest")).get("test", {})

    async def apply_monit_test(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Upsert (or delete) a Monit test by `name`; optionally attach it to the system service;
        then reconfigure.

        `attach_to_system` ("1") is a directive, stripped from the test payload before it is sent.
        Identity is `name` (1 match -> setTest; none -> addTest; many -> refuse). `operation ==
        "delete"` resolves the same identity and POSTs monit/settings/delTest/{uuid} (for the revert
        path); an absent test is a clean no-op. Limitation: delete does NOT detach the test from a
        Monit service first (rare; the subsequent reconfigure tolerates a dangling reference).
        dry_run mutates nothing."""
        if operation == "delete":
            # Short-circuit before the attach_to_system pop: delete reads `name` from the raw payload.
            name = str(payload.get("name", ""))
            if dry_run:
                return {"dry_run": True, "operation": "delete", "name": name}
            uuid_ = await self._resolve_monit_test_uuid(name)
            if uuid_ is None:
                return {"dry_run": False, "operation": "delete", "result": "absent"}
            res = await self._post(f"monit/settings/delTest/{uuid_}", {})
            await self._post("monit/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
            return {"dry_run": False, "operation": "delete", "result": res}
        payload = dict(payload)
        attach = str(payload.pop("attach_to_system", "0")) in ("1", "true", "True")
        name = str(payload.get("name", ""))
        if dry_run:
            return {"dry_run": True, "name": name, "attach_to_system": attach}
        uuid_ = await self._resolve_monit_test_uuid(name)
        if uuid_ is None:
            res = await self._post("monit/settings/addTest", {"test": payload})
            test_uuid, op = res.get("uuid"), "add"
        else:
            res = await self._post(f"monit/settings/setTest/{uuid_}", {"test": payload})
            test_uuid, op = uuid_, "set"
        _raise_on_failed(res, "monit test")
        if attach and test_uuid:
            await self._attach_test_to_system(test_uuid)
        await self._post("monit/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": op, "attached": bool(attach), "result": res}

    async def _resolve_system_service_uuid(self) -> str:
        """The Monit `system`-type service uuid. Refuse (ApiError) if zero or >1 (never mutate on doubt)."""
        data = await self._post("monit/settings/searchService", {"current": 1, "rowCount": 1000})
        matches = [r for r in data.get("rows", []) if str(r.get("type", "")).lower() == "system"]
        if len(matches) != 1:
            raise ApiError(0, f"monit system service not uniquely resolvable ({len(matches)})")
        return matches[0]["uuid"]

    async def _attach_test_to_system(self, test_uuid: str) -> None:
        """Add `test_uuid` to the system service's tests (partial merge). Idempotent."""
        sid = await self._resolve_system_service_uuid()
        svc = (await self._get(f"monit/settings/getService/{sid}")).get("service", {})
        tests = svc.get("tests", {})
        selected = [k for k, v in tests.items() if isinstance(v, dict) and str(v.get("selected")) in ("1", "True")]
        if test_uuid in selected:
            return
        selected.append(test_uuid)
        await self._post(f"monit/settings/setService/{sid}", {"service": {"tests": ",".join(selected)}})

    async def _resolve_monit_test_uuid(self, name: str) -> str | None:
        """Resolve a Monit test by EXACT name. None if absent; ApiError if many (never mutate on doubt)."""
        if not name:
            raise ApiError(0, "monit test name required (it is the test identity)")
        data = await self._post(
            "monit/settings/searchTest", {"current": 1, "rowCount": 1000, "searchPhrase": name})
        matches = [r for r in data.get("rows", []) if r.get("name") == name]
        if len(matches) > 1:
            raise ApiError(0, f"monit test '{name}' not uniquely resolvable ({len(matches)} matches)")
        if not matches:
            return None
        uuid_ = str(matches[0].get("uuid", ""))
        # Box-sourced, but guard before it is embedded in a setTest/delTest URL path (catchable ApiError).
        if not _OPN_UUID_RE.match(uuid_):
            raise ApiError(0, f"monit test '{name}' resolved to an unsafe uuid")
        return uuid_

    async def list_ids_rulesets(self) -> list[dict]:
        """Catalog of installed Suricata/IDS rulesets: [{filename, description, enabled, ...}]."""
        return (await self._get("ids/settings/listRulesets")).get("rows", [])

    async def apply_ids_rulesets(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Enable the listed IDS rulesets (additive/non-destructive), then reload the engine.

        Verified against OPNsense 26.1.9: POST ids/settings/toggleRuleset/{filename}/1 enables one
        ruleset; ids/service/reconfigure reloads Suricata. Each filename is charset-validated
        (anti path-injection) before it is embedded in the URL path. dry_run performs NO mutation."""
        raw = payload.get("rulesets", [])
        if not isinstance(raw, (list, tuple)):
            raise ApiError(0, "rulesets must be a list")
        # Validate EVERY filename up-front (charset/anti path-injection) so the connector is
        # self-defending and never partially-applies a list with a bad entry mid-loop.
        rulesets = [self._ruleset_name(name) for name in raw]
        if dry_run:
            return {"dry_run": True, "rulesets": rulesets}
        for name in rulesets:
            await self._post(f"ids/settings/toggleRuleset/{name}/1", {})
        await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "enabled": rulesets}

    async def apply_ids_policy(self, operation: str, payload: dict, *, dry_run: bool = True) -> dict:
        """Upsert an IDS policy by `description` (or delete it), then reload Suricata.

        Identity = description (1 match -> setPolicy; none -> addPolicy; many -> refuse). `rulesets`
        filenames are resolved to the device's ENABLED ruleset-file uuids; an absent/disabled ruleset
        raises ApiError (never a partial apply). dry_run performs NO mutation. RUNTIME VERIFICATION
        REQUIRED for the rulesets/content serialization (no policies/rules on the box to confirm against)."""
        if operation not in ("set", "add", "delete"):
            raise ApiError(0, f"unknown ids policy operation: {operation!r}")
        description = str(payload.get("description", ""))
        if dry_run:
            return {"dry_run": True, "operation": operation, "description": description}
        if operation == "delete":
            uuid_ = await self._resolve_policy_uuid(description)
            if uuid_ is None:
                return {"dry_run": False, "operation": "delete", "result": "absent"}
            res = await self._post(f"ids/settings/delPolicy/{uuid_}", {})
            await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
            return {"dry_run": False, "operation": "delete", "result": res}
        # Resolve the identity FIRST (fail fast on ambiguity) before building the body — mirrors
        # apply_firewall_rule / apply_monit_test and avoids a redundant getPolicy on a doomed apply.
        uuid_ = await self._resolve_policy_uuid(description)
        policy = await self._serialize_policy(payload)
        if uuid_ is None:
            res = await self._post("ids/settings/addPolicy", {"policy": policy})
            op = "add"
        else:
            res = await self._post(f"ids/settings/setPolicy/{uuid_}", {"policy": policy})
            op = "set"
        _raise_on_failed(res, "ids policy")
        await self._post("ids/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return {"dry_run": False, "operation": op, "result": res}

    async def _resolve_policy_uuid(self, description: str) -> str | None:
        """Resolve an IDS policy by EXACT description. None if absent; ApiError if many (never mutate on doubt)."""
        if not description:
            raise ApiError(0, "ids policy description required (it is the policy identity)")
        data = await self._post(
            "ids/settings/searchPolicy", {"current": 1, "rowCount": 1000, "searchPhrase": description})
        matches = [r for r in data.get("rows", []) if r.get("description") == description]
        if len(matches) > 1:
            raise ApiError(0, f"ids policy '{description}' not uniquely resolvable ({len(matches)} matches)")
        if not matches:
            return None
        uuid_ = str(matches[0].get("uuid", ""))
        # Box-sourced, but guard before it is embedded in a URL path (catchable ApiError, not a bare
        # ValueError that would strand the change in 'applying').
        if not _OPN_UUID_RE.match(uuid_):
            raise ApiError(0, f"ids policy '{description}' resolved to an unsafe uuid")
        return uuid_

    async def _resolve_ruleset_file_uuids(self, filenames: list[str]) -> list[str]:
        """Map each ruleset FILENAME to its ENABLED file-uuid via the policy model's relation option map.

        GET ids/settings/getPolicy returns policy.rulesets as {file_uuid: {"value": filename, "selected": …}}
        for every enabled ruleset. A filename absent from that map is not enabled -> ApiError."""
        if not filenames:
            return []
        options = (await self._get("ids/settings/getPolicy")).get("policy", {}).get("rulesets", {})
        by_name: dict[str, str] = {}
        if isinstance(options, dict):
            for fuuid, meta in options.items():
                name = meta.get("value") if isinstance(meta, dict) else None
                # Guard the box-sourced uuid before it is comma-joined into the policy body (a stray
                # comma/control char would silently split the field); an unsafe uuid just won't resolve.
                if name and _OPN_UUID_RE.match(str(fuuid)):
                    by_name[name] = str(fuuid)
        out = []
        for name in filenames:
            self._ruleset_name(name)                       # charset guard
            uuid_ = by_name.get(name)
            if uuid_ is None:
                raise ApiError(0, f"ruleset '{name}' must be enabled before a policy can reference it")
            out.append(uuid_)
        return out

    async def _serialize_policy(self, payload: dict) -> dict:
        """Build the OPNsense addPolicy/setPolicy body from a portable policy. Multi-fields are
        comma-joined; rulesets filenames are resolved to enabled file-uuids."""
        actions = payload.get("action", []) or []
        rulesets = await self._resolve_ruleset_file_uuids(payload.get("rulesets", []) or [])
        # OPNsense's PolicyContentField is an OptionField keyed by "<metadata_key>.<value>" tokens
        # (verified on a real 26.1.9 box via getPolicy.content); the selected tokens are comma-joined.
        # The portable body carries content as {metadata_key: [values]}; flatten it to those tokens.
        content = payload.get("content", {}) or {}
        content_tokens = [f"{key}.{value}" for key, values in content.items() for value in (values or [])]
        return {
            "enabled": str(payload.get("enabled", "1")),
            "prio": str(payload.get("prio", "0")),
            "action": ",".join(actions),
            "rulesets": ",".join(rulesets),
            "content": ",".join(content_tokens),
            "new_action": str(payload.get("new_action", "alert")),
            "description": str(payload.get("description", "")),
        }

    @staticmethod
    def _ruleset_name(name: str) -> str:
        if not isinstance(name, str) or not _RULESET_NAME_RE.match(name):
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

    async def get_firewall_blocks(self, since: datetime | None = None) -> list[dict]:
        """Blocked-traffic attacker IPs from the firewall log. `since` filtered downstream."""
        return await self._capability("firewall_blocks")

    async def get_auth_failures(self, since: datetime | None = None) -> list[dict]:
        """Failed-login attempts (attacker IP + user) from the audit log. `since` filtered downstream."""
        return await self._capability("auth_failures")

    async def get_service_events(self, since: datetime | None = None) -> list[dict]:
        """Reliability events (reboot / service crash-restart / disk-FS) classified out of the system
        log. `since` filtered downstream."""
        return await self._capability("service_events")

    async def get_config_changes(self, since: datetime | None = None) -> list[dict]:
        """Config-change audit events (who/what/when, channel-attributed) from the box audit log.
        `since` is accepted for caller convenience; filtering/dedup happen downstream."""
        return await self._capability("config_changes")

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

    async def import_ca(self, ca_cert_pem: str, *, descr: str) -> str:
        """Import a CA public cert into the box's trust store (so it trusts the receiver). Returns uuid."""
        res = await self._post("trust/ca/add",
                               {"ca": {"action": "existing", "descr": descr, "crt_payload": ca_cert_pem}})
        return res.get("uuid", "")

    async def import_cert(self, cert_pem: str, key_pem: str, *, descr: str) -> str:
        """Import a client cert + key into the box's trust store (the syslog client cert). Returns uuid."""
        res = await self._post("trust/cert/add",
                               {"cert": {"action": "import", "descr": descr,
                                         "crt_payload": cert_pem, "prv_payload": key_pem}})
        return res.get("uuid", "")

    async def add_syslog_destination(self, *, hostname: str, port: int, certificate_uuid: str,
                                     description: str = "OPNGMS log forwarding") -> str:
        """Add a TLS (mTLS) remote-syslog destination presenting the given client cert; reconfigure.

        Verified live on OPNsense 26.1.9: the syslog destination's `certificate` field references the
        cert by its legacy **refid**, NOT the MVC uuid that trust/cert/add returns — so we resolve the
        refid first. We also RAISE on a rejected destination; otherwise a validation failure (e.g. a
        wrong cert reference) silently leaves the box with an imported cert but no destination — which
        is exactly the bug this fixes. Returns the new destination uuid."""
        cert = await self._get(f"trust/cert/get/{_safe_uuid(certificate_uuid)}")
        cert_ref = (cert.get("cert", {}) or {}).get("refid", "")
        if not cert_ref:
            raise ApiError(0, f"imported cert {certificate_uuid} has no refid on the device")
        res = await self._post("syslog/settings/addDestination", {"destination": {
            "enabled": "1", "transport": "tls4", "program": "", "level": "", "facility": "",
            "hostname": hostname, "certificate": cert_ref, "port": str(port),
            "rfc5424": "1", "description": description}})
        uuid_ = res.get("uuid", "")
        if res.get("result") != "saved" or not uuid_:
            raise ApiError(0, f"syslog addDestination rejected: {res.get('validations') or res}")
        await self._post("syslog/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return uuid_

    async def delete_syslog_destination(self, dest_uuid: str) -> dict:
        """Delete a remote-syslog destination by uuid and reconfigure."""
        dest_uuid = _safe_uuid(dest_uuid)
        res = await self._post(f"syslog/settings/delDestination/{dest_uuid}", {})
        await self._post("syslog/service/reconfigure", {}, timeout=RECONFIGURE_TIMEOUT)
        return res

    async def delete_cert(self, cert_uuid: str) -> dict:
        """Delete a certificate from the trust store by uuid."""
        cert_uuid = _safe_uuid(cert_uuid)
        return await self._post(f"trust/cert/del/{cert_uuid}", {})
