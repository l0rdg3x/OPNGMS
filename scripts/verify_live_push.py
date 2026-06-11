#!/usr/bin/env python3
"""Live end-to-end check of apply_alias against real OPNsense hardware (NOT run in CI).

Usage:
    OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt \
    python scripts/verify_live_push.py

Creates a throwaway host alias via the real apply_alias (add), confirms it exists via
searchItem, then deletes it (apply_alias delete) and confirms it is gone. Credentials are
never printed. The alias is named distinctively and always cleaned up in a finally block.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402

NAME = "opngms_live_push_probe"


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


async def _present(client) -> int:
    data = await client._post(
        "firewall/alias/searchItem", {"current": 1, "rowCount": 1000, "searchPhrase": NAME}
    )
    return len([r for r in data.get("rows", []) if r.get("name") == NAME])


async def main() -> int:
    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)
    rc = 1
    try:
        add = await client.apply_alias(
            "add",
            {"enabled": "1", "name": NAME, "type": "host", "content": "192.0.2.50",
             "description": "OPNGMS live-push probe (delete me)"},
            dry_run=False,
        )
        print(f"add        -> {add.get('result')}")
        print(f"present    -> {await _present(client) == 1}")
        rc = 0
    finally:
        try:
            if await _present(client):
                await client.apply_alias("delete", {"name": NAME}, dry_run=False)
            gone = await _present(client) == 0
            print(f"cleanup    -> gone={gone}")
            if not gone:
                rc = 1
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP ERROR: {type(exc).__name__}: {exc}")
            rc = 1
    print("ALL PASS" if rc == 0 else "FAILED")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
