"""Resolve a capability to its concrete EndpointSpec for a given device (edition, version)."""
from app.connectors.opnsense import parsers
from app.connectors.opnsense.profiles import CAPABILITIES, EndpointSpec

# Unknown/unparseable version -> assume the newest profile (most likely correct for a 2026+
# fleet; never selects a legacy rule with a bounded max_version).
_NEWEST = (9999, 99, 99, 99)


class CapabilityResolver:
    def __init__(self, edition: str, version: str, rules: dict | None = None) -> None:
        self.edition = (edition or "community").strip().lower()
        v = parsers.parse_version(version)
        self.vtuple = _NEWEST if v == (0, 0, 0, 0) else v
        self._rules = rules if rules is not None else CAPABILITIES

    def resolve(self, capability: str) -> EndpointSpec:
        rules = self._rules.get(capability)
        if not rules:
            raise ValueError(f"unknown or empty capability profile: {capability!r}")
        for rule in rules:
            if rule.edition not in ("any", self.edition):
                continue
            if rule.min_version is not None and self.vtuple < rule.min_version:
                continue
            if rule.max_version is not None and self.vtuple >= rule.max_version:
                continue
            return rule.spec
        return rules[-1].spec   # guaranteed: last rule is the unconstrained default
