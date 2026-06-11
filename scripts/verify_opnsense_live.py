#!/usr/bin/env python3
"""Read-only OPNsense connector verification against real hardware.

Usage:
    OPNSENSE_URL=https://192.168.1.82 \
    OPNSENSE_KEYFILE=~/path/OPNsense.apikey.txt \
    python scripts/verify_opnsense_live.py

The key file has two lines: `key=...` and `secret=...`. Credentials are never printed.
Exercises every read path of OpnsenseClient and prints a PASS/FAIL line per method. With
--dump <dir>, writes the raw JSON responses to <dir> for refreshing test fixtures.

Not run in CI (no hardware). It is a developer tool to re-verify and re-capture fixtures
after an OPNsense upgrade or against a different edition (Community / Business).
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402


def _read_creds(keyfile: str) -> tuple[str, str]:
    key = secret = ""
    for line in Path(keyfile).expanduser().read_text().splitlines():
        if line.startswith("key="):
            key = line[4:].strip()
        elif line.startswith("secret="):
            secret = line[7:].strip()
    if not key or not secret:
        raise SystemExit("key/secret not found in key file")
    return key, secret


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", metavar="DIR", help="write raw JSON responses to DIR")
    args = ap.parse_args()

    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)

    ident = await client.get_device_identity()
    print(f"IDENTITY  edition={ident.edition} version={ident.version} series={ident.series}\n")
    client.set_identity(ident.edition, ident.version)

    checks = {
        "test_connection": client.test_connection(),
        "get_plugin_info": client.get_plugin_info(),
        "get_system_info": client.get_system_info(),
        "get_interfaces": client.get_interfaces(),
        "get_gateways": client.get_gateways(),
        "get_vpn_status": client.get_vpn_status(),
        "get_ids_alerts": client.get_ids_alerts(),
        "get_dns_events": client.get_dns_events(),
    }
    failures = 0
    results = {}
    for name, coro in checks.items():
        try:
            value = await coro
            results[name] = value
            if isinstance(value, (str, type(None))):
                summary = value
            elif isinstance(value, list):
                summary = f"{len(value)} items"
            else:
                summary = "ok"
            print(f"PASS  {name:18} -> {summary}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL  {name:18} -> {type(exc).__name__}: {exc}")

    if args.dump:
        d = Path(args.dump)
        d.mkdir(parents=True, exist_ok=True)
        (d / "results.json").write_text(json.dumps(results, default=str, indent=2))
        print(f"\nWrote {d / 'results.json'}")
    print(f"\n{'ALL PASS' if failures == 0 else f'{failures} FAILED'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
