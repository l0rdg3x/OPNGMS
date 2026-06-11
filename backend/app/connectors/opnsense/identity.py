"""Detect an OPNsense device's edition + version from core/firmware/status."""
from dataclasses import dataclass

from app.connectors.opnsense import parsers


@dataclass(frozen=True)
class DeviceIdentity:
    edition: str   # "community" | "business" | "devel"
    version: str   # e.g. "26.1.9" or "26.1.9_1"
    series: str    # e.g. "26.1"


def parse_identity(firmware_status) -> DeviceIdentity:
    """Map a core/firmware/status payload to a DeviceIdentity. Never raises on any input shape.

    Edition signal: PRIMARY is product_id ("opnsense-business"->business, "opnsense-devel"->devel,
    any other non-empty id e.g. "opnsense"->community). Only when product_id is absent/empty do we
    fall back to "business" appearing in product_repos/product_name. Business values are inferred
    pending a real Business box."""
    fs = firmware_status if isinstance(firmware_status, dict) else {}
    product = fs.get("product")
    if not isinstance(product, dict):
        product = {}
    pid = str(product.get("product_id", "")).lower()
    if "business" in pid:
        edition = "business"
    elif "devel" in pid:
        edition = "devel"
    elif pid:                       # a recognized non-business product_id (e.g. "opnsense")
        edition = "community"
    else:                           # product_id absent -> fall back to repos/name
        blob = f"{str(product.get('product_repos', '')).lower()} {str(product.get('product_name', '')).lower()}"
        edition = "business" if "business" in blob else "community"
    version = product.get("product_version") or ""
    series = product.get("product_series") or (parsers.series_of(version) if version else "")
    return DeviceIdentity(edition=edition, version=version, series=series)
