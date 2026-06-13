"""Map raw metric labels (OPNsense interface/gateway/VPN identifiers) to their assigned names.

Health charts label series by the identifier the poll returns — interfaces by the OPNsense key
(`wan`/`lan`/`opt1`), gateways by the gateway name, VPN by the tunnel name. The human-assigned
descriptions live in the device's `config.xml` (already captured in `config_snapshots`):

  <interfaces><opt1><descr>DMZ</descr>...</opt1>...</interfaces>
  <gateways><gateway_item><name>WAN_GW</name><descr>Primary fiber</descr>...</gateway_item></gateways>
  <OpenVPN>/<wireguard> instances carry a <description>/<name>.

We parse those into a flat ``{raw_label: friendly_name}`` map. Only entries that actually have a
non-empty description are included; the caller falls back to the raw identifier for everything else.
"""
from __future__ import annotations

import gzip
import uuid

from defusedxml.ElementTree import fromstring as _parse_xml  # XXE / billion-laughs safe
from sqlalchemy.ext.asyncio import AsyncSession


def _text(node, tag: str) -> str:
    child = node.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def friendly_labels(config_xml: str) -> dict[str, str]:
    """Parse a device config.xml into ``{raw_metric_label: assigned_name}`` (descr-bearing entries only)."""
    if not config_xml:
        return {}
    try:
        root = _parse_xml(config_xml)
    except Exception:
        return {}
    labels: dict[str, str] = {}

    # Interfaces: child tag is the identifier (wan/lan/opt1) = the iface.* metric label.
    interfaces = root.find("interfaces")
    if interfaces is not None:
        for iface in interfaces:
            descr = _text(iface, "descr")
            if iface.tag and descr:
                labels[iface.tag] = descr

    # Gateways: gateway_item name = the gateway.* metric label; map to its descr when set.
    gateways = root.find("gateways")
    if gateways is not None:
        for item in gateways.findall("gateway_item"):
            name = _text(item, "name")
            descr = _text(item, "descr")
            if name and descr:
                labels[name] = descr

    # VPN (best-effort): the vpn.* metric label is the WireGuard tunnel name (the connector polls
    # WireGuard); map it to a distinct description when one is set.
    wg = root.find("OPNsense/wireguard/server/servers")
    if wg is not None:
        for server in wg.findall("server"):
            name = _text(server, "name")
            descr = _text(server, "description")
            if name and descr and name != descr:
                labels[name] = descr

    return labels


async def device_friendly_labels(
    session: AsyncSession, tenant_id: uuid.UUID, device_id: uuid.UUID
) -> dict[str, str]:
    """`friendly_labels` for a device's latest config snapshot. Degrades to {} (no snapshot / a blob
    encrypted under a retired key) — used by both the metric-labels API and the report builder."""
    from app.core import crypto
    from app.repositories.config_snapshot import ConfigSnapshotRepository

    snap = await ConfigSnapshotRepository(session, tenant_id).latest(device_id)
    if snap is None:
        return {}
    try:
        xml = gzip.decompress(crypto.decrypt_bytes(snap.content_enc)).decode("utf-8")
    except Exception:
        return {}
    return friendly_labels(xml)
