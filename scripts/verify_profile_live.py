#!/usr/bin/env python3
"""Live check that a profile's fan-out drives MULTIPLE real alias writes (NOT in CI). Builds two
effective firewall_alias bodies (the profile's two members), validates each with the engine,
applies both via apply_alias, confirms BOTH aliases land with correct (list) content, then deletes
both (guaranteed cleanup). Credentials are never printed.

Usage:
    OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt \
    python scripts/verify_profile_live.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402
from app.services.templates import validate_alias_body  # noqa: E402

# Two profile members: alias A (single IP) and alias B (two IPs)
ALIAS_A = "opngms_profile_probe_a"
WANT_A = ["192.0.2.71"]

ALIAS_B = "opngms_profile_probe_b"
WANT_B = ["192.0.2.72", "192.0.2.73"]


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


async def _row(client, name: str) -> dict | None:
    data = await client._post(
        "firewall/alias/searchItem", {"current": 1, "rowCount": 1000, "searchPhrase": name}
    )
    rows = [r for r in data.get("rows", []) if r.get("name") == name]
    return rows[0] if rows else None


async def main() -> int:
    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)

    # Build effective bodies for both profile members and validate each with the engine's own
    # validator — exactly what the profile fan-out path does at apply time.
    body_a = {
        "name": ALIAS_A,
        "type": "host",
        "content": WANT_A,
        "description": "OPNGMS profile probe (delete me)",
    }
    body_b = {
        "name": ALIAS_B,
        "type": "host",
        "content": WANT_B,
        "description": "OPNGMS profile probe (delete me)",
    }
    validate_alias_body(body_a)
    validate_alias_body(body_b)

    rc = 1
    try:
        # ── alias A ──────────────────────────────────────────────────────────
        add_a = await client.apply_alias("add", {"enabled": "1", **body_a}, dry_run=False)
        print(f"[A] add        -> {add_a.get('result')}")
        row_a = await _row(client, ALIAS_A)
        present_a = row_a is not None
        content_str_a = "" if row_a is None else str(row_a.get("content", ""))
        content_ok_a = present_a and all(ip in content_str_a for ip in WANT_A)
        print(f"[A] present    -> {present_a}")
        print(f"[A] content_ok -> {content_ok_a}  (row content: {content_str_a!r})")

        # ── alias B ──────────────────────────────────────────────────────────
        add_b = await client.apply_alias("add", {"enabled": "1", **body_b}, dry_run=False)
        print(f"[B] add        -> {add_b.get('result')}")
        row_b = await _row(client, ALIAS_B)
        present_b = row_b is not None
        content_str_b = "" if row_b is None else str(row_b.get("content", ""))
        content_ok_b = present_b and all(ip in content_str_b for ip in WANT_B)
        print(f"[B] present    -> {present_b}")
        print(f"[B] content_ok -> {content_ok_b}  (row content: {content_str_b!r})")

        rc = 0 if (content_ok_a and content_ok_b) else 1
    finally:
        cleanup_ok = True
        try:
            if await _row(client, ALIAS_A) is not None:
                await client.apply_alias("delete", {"name": ALIAS_A}, dry_run=False)
            gone_a = await _row(client, ALIAS_A) is None
            print(f"[A] cleanup    -> gone={gone_a}")
            if not gone_a:
                cleanup_ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP ERROR [A]: {type(exc).__name__}: {exc}")
            cleanup_ok = False
        try:
            if await _row(client, ALIAS_B) is not None:
                await client.apply_alias("delete", {"name": ALIAS_B}, dry_run=False)
            gone_b = await _row(client, ALIAS_B) is None
            print(f"[B] cleanup    -> gone={gone_b}")
            if not gone_b:
                cleanup_ok = False
        except Exception as exc:  # noqa: BLE001
            print(f"CLEANUP ERROR [B]: {type(exc).__name__}: {exc}")
            cleanup_ok = False
        if not cleanup_ok:
            rc = 1
    print("ALL PASS" if rc == 0 else "FAILED")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
