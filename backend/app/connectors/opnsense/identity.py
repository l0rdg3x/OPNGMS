"""Detect an OPNsense device's edition + version from core/firmware/status."""
from dataclasses import dataclass

from app.connectors.opnsense import parsers


@dataclass(frozen=True)
class DeviceIdentity:
    edition: str   # "community" | "business" | "devel"
    version: str   # e.g. "26.1.9" or "26.1.9_1"
    series: str    # e.g. "26.1"


def parse_identity(firmware_status: dict) -> DeviceIdentity:
    """Map a core/firmware/status payload to a DeviceIdentity. Never raises.

    Edition signal: product_id ("opnsense" vs "opnsense-business" vs "opnsense-devel"), with a
    defensive fallback to product_repos/product_name containing "business". Business values are
    inferred pending a real Business box."""
    product = (firmware_status or {}).get("product", {}) or {}
    pid = str(product.get("product_id", "")).lower()
    blob = f"{pid} {str(product.get('product_repos', '')).lower()} {str(product.get('product_name', '')).lower()}"
    if "business" in blob:
        edition = "business"
    elif "devel" in pid:
        edition = "devel"
    else:
        edition = "community"
    version = product.get("product_version") or ""
    series = product.get("product_series") or parsers.series_of(version)
    return DeviceIdentity(edition=edition, version=version, series=series)
