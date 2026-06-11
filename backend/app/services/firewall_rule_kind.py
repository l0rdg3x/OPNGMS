"""Register the curated `firewall_rule` template kind (Rules[new]) + its config-change applier.

Body = a portable filter rule (all portable fields). `interface` is an apply-time binding (empty =
floating). Identity = `description`; the connector upserts by (description, interface)."""
import re

from app.services.config_apply import register_change_applier
from app.services.templates import InvalidTemplateError, TemplateKind, register_template_kind

_ACTIONS = {"pass", "block", "reject"}
_DIRECTIONS = {"in", "out"}
_IPPROTOCOLS = {"inet", "inet6", "inet46"}
# net: any | IP/CIDR (v4/v6) | alias name. port: empty | port/range | alias. Conservative, no spaces.
_NET_RE = re.compile(r"\A[A-Za-z0-9_.:/-]+\Z")
_PORT_RE = re.compile(r"\A[A-Za-z0-9_:-]*\Z")
_IFACE_RE = re.compile(r"\A[A-Za-z0-9_]*\Z")


def _validate(body: dict) -> None:
    body = body or {}
    if not str(body.get("description", "")).strip():
        raise InvalidTemplateError("firewall rule 'description' is required (it is the rule identity)")
    if body.get("action") not in _ACTIONS:
        raise InvalidTemplateError(f"'action' must be one of {sorted(_ACTIONS)}")
    if body.get("direction") not in _DIRECTIONS:
        raise InvalidTemplateError(f"'direction' must be one of {sorted(_DIRECTIONS)}")
    if body.get("ipprotocol") not in _IPPROTOCOLS:
        raise InvalidTemplateError(f"'ipprotocol' must be one of {sorted(_IPPROTOCOLS)}")
    for f in ("source_net", "destination_net"):
        v = str(body.get(f, "any"))
        if not _NET_RE.match(v):
            raise InvalidTemplateError(f"'{f}' must be any / an IP-CIDR / an alias name")
    for f in ("source_port", "destination_port"):
        v = str(body.get(f, ""))
        if not _PORT_RE.match(v):
            raise InvalidTemplateError(f"'{f}' must be empty / a port-range / an alias name")
    if not _IFACE_RE.match(str(body.get("interface", ""))):
        raise InvalidTemplateError("'interface' has an invalid value")


register_template_kind("firewall_rule", TemplateKind(
    validate=_validate,
    change_kind="firewall_rule",
    to_change=lambda body: ("set", str(body.get("description", "")), body),
    pinned=("description",),
    bind=lambda body, b: {**body, "interface": b.get("interface", "")},
))


async def _apply_firewall_rule(client, operation: str, payload: dict, *, dry_run: bool) -> dict:
    return await client.apply_firewall_rule(operation, payload, dry_run=dry_run)


register_change_applier("firewall_rule", _apply_firewall_rule)
