#!/usr/bin/env python3
"""Live plugin install/remove check against real OPNsense hardware (NOT in CI).

Usage:
    OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt \
    python scripts/verify_plugin_live.py [plugin-name]

Installs a small throwaway plugin (default os-acme-client), polls upgradestatus to completion,
confirms it is installed, then removes it (guaranteed cleanup). Requires the device to be up to
date (OPNsense blocks plugin installs otherwise) -- it prints a clear message and aborts cleanly
if updates are pending. Credentials are never printed.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402
from app.services.firmware_action import poll_until_done, updates_pending  # noqa: E402


def _creds(keyfile: str) -> tuple[str, str]:
    key = secret = ""
    for line in Path(keyfile).expanduser().read_text().splitlines():
        if line.startswith("key="):
            key = line[4:].strip()
        elif line.startswith("secret="):
            secret = line[7:].strip()
    if not key or not secret:
        raise SystemExit("key/secret not found in key file")
    return key, secret


async def _installed(client, name) -> bool:
    info = await client.get_plugin_info()
    return name in info.get("plugins", [])


async def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "os-acme-client"
    base = os.environ["OPNSENSE_URL"]
    key, secret = _creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)
    await client.firmware_check()
    await poll_until_done(client)                          # wait for the mirror check (serialized)
    if updates_pending(await client.firmware_status_raw()):
        print("ABORT: device has pending firmware updates -- OPNsense blocks plugin installs. Update first.")
        print("SKIPPED (not up to date)")
        return 2
    rc = 1
    try:
        print(f"install {name} ...")
        await client.plugin_install(name)
        await poll_until_done(client)
        ok = await _installed(client, name)
        print(f"installed -> {ok}")
        rc = 0 if ok else 1
    finally:
        try:
            print(f"remove {name} ...")
            await client.plugin_remove(name)
            await poll_until_done(client)
            gone = not await _installed(client, name)
            print(f"cleanup -> removed={gone}")
            if not gone:
                rc = 1
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP ERROR: {type(exc).__name__}: {exc}")
            rc = 1
    print("ALL PASS" if rc == 0 else "FAILED" if rc == 1 else "SKIPPED (not up to date)")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
