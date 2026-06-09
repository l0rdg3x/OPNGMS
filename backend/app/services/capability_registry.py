"""Small, extensible registry mapping OPNsense plugin/module ids to capability descriptors.

Seeded with common core/plugins; unknown ids pass through with a generic descriptor.
The exhaustive field-level per-version schema is out of scope (deferred to 4D, device-sourced).
"""

_REGISTRY: dict[str, dict] = {
    "os-wireguard": {"label": "WireGuard VPN", "area": "vpn/wireguard"},
    "os-openvpn": {"label": "OpenVPN", "area": "vpn/openvpn"},
    "os-firewall": {"label": "Firewall rules (API)", "area": "firewall"},
    "os-dhcp": {"label": "DHCP", "area": "services/dhcp"},
    "os-unbound": {"label": "Unbound DNS", "area": "services/unbound"},
    "os-ids": {"label": "Intrusion Detection (Suricata)", "area": "ids"},
    "os-haproxy": {"label": "HAProxy", "area": "services/haproxy"},
}


def describe(plugin_id: str) -> dict:
    base = _REGISTRY.get(plugin_id)
    if base is None:
        return {"id": plugin_id, "label": plugin_id, "area": ""}  # generic pass-through
    return {"id": plugin_id, **base}
