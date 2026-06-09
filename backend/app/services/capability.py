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


def build_inventory(xml: str, opnsense_version: str, plugin_info: dict) -> dict:
    root = _parse_xml(xml)
    configured_sections = [el.tag for el in list(root) if el.tag != "revision"]
    available = [describe(pid) for pid in plugin_info.get("plugins", [])]
    return {
        "opnsense_version": opnsense_version,
        "interfaces": _interfaces(root),
        "configured_sections": configured_sections,
        "available_capabilities": available,
    }
