"""Per-device capability inventory: empirical (from config) + live probe + registry."""

import xml.etree.ElementTree as ET  # type annotations only — NOT for parsing

from defusedxml.ElementTree import fromstring as _parse_xml

from app.services.capability_registry import describe


def _interfaces(root: ET.Element) -> list[dict]:
    out: list[dict] = []
    ifaces = root.find("interfaces")
    if ifaces is None:
        return out
    for el in list(ifaces):
        out.append({
            "name": el.tag,
            "nic": (el.findtext("if") or "").strip(),
            "description": (el.findtext("descr") or "").strip(),
        })
    return out


def build_inventory(xml: str, opnsense_version: str, plugin_info: dict, edition: str = "") -> dict:
    root = _parse_xml(xml)
    configured_sections = [el.tag for el in list(root) if el.tag != "revision"]
    # NOTE: `available_capabilities` is currently plugin-derived only. Gating it by the device's
    # (edition, version) against the connector's CAPABILITIES matrix is deferred until
    # edition/version-specific capabilities actually exist (e.g. when a Business profile adds
    # them) — today every capability is edition="any", so such gating would be a no-op.
    available = [describe(pid) for pid in plugin_info.get("plugins", [])]
    return {
        "opnsense_version": opnsense_version,
        "edition": edition,
        "interfaces": _interfaces(root),
        "configured_sections": configured_sections,
        "available_capabilities": available,
    }
