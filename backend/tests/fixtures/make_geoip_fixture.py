#!/usr/bin/env python
"""Generate the tiny GeoIP fixture mmdb used by the test suite (vendored as geoip-test.mmdb).

This produces a minimal DB-IP-Lite-Country-shaped mmdb mapping a handful of /24 ranges to ISO alpha-2
country codes, so `tests/test_geoip.py` / `tests/test_attacker_countries*.py` never need the live
release asset or any network. The schema mirrors DB-IP/MaxMind:

    reader.get(ip) -> {"country": {"iso_code": "RU"}}

Re-run after installing the dev extras (`pip install -e .[dev]` brings in `mmdb-writer`):

    python tests/fixtures/make_geoip_fixture.py

It writes `geoip-test.mmdb` next to this script. The .mmdb is committed; regenerating it should be a
no-op unless the mappings below change.
"""
from __future__ import annotations

from pathlib import Path

from mmdb_writer import MMDBWriter
from netaddr import IPSet

# /24 -> ISO alpha-2. Kept tiny on purpose; add ranges here if a test needs another country.
# NB: the JP range must be genuinely *global* space — Python's ipaddress now treats the documentation
# blocks (203.0.113.0/24, 198.51.100.0/24) as private, so the GeoIp.country() private-filter would
# collapse them to the PRIVATE sentinel before the db is ever consulted. 133.11.0.0/16 (University of
# Tokyo) is real, globally-routable JP space and is left out of the GeoIp private filter.
RANGES: dict[str, str] = {
    "1.0.0.0/24": "US",
    "77.88.8.0/24": "RU",      # Yandex DNS (77.88.8.8)
    "5.255.255.0/24": "RU",
    "8.8.8.0/24": "US",        # Google DNS (8.8.8.8)
    "133.11.11.0/24": "JP",    # real globally-routable JP space (University of Tokyo)
}


def build(out_path: Path) -> None:
    writer = MMDBWriter(
        ip_version=6,
        database_type="DBIP-Country-Lite",
        languages=["en"],
        # Store IPv4 in the IPv6 tree (::/96) so a single reader handles both families.
        ipv4_compatible=True,
    )
    for cidr, code in RANGES.items():
        writer.insert_network(IPSet([cidr]), {"country": {"iso_code": code}})
    writer.to_db_file(str(out_path))


if __name__ == "__main__":
    target = Path(__file__).with_name("geoip-test.mmdb")
    build(target)
    print(f"wrote {target} ({target.stat().st_size} bytes)")
