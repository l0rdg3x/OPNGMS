#!/usr/bin/env python3
"""Live check that the template engine's effective firewall_alias body drives the real alias
write (NOT in CI). Builds an effective body with content as a LIST (the template form),
validates it with the engine's validator, applies it via apply_alias, confirms the alias AND
its content landed, then deletes it (guaranteed cleanup). Credentials are never printed.

Usage:
    OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt \
    python scripts/verify_template_live.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402
from app.services.templates import effective_body, validate_alias_body  # noqa: E402

NAME = "opngms_tpl_probe"
WANT = ["192.0.2.61", "192.0.2.62"]   # a LIST (the template content form)


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


async def _row(client) -> dict | None:
    data = await client._post(
        "firewall/alias/searchItem", {"current": 1, "rowCount": 1000, "searchPhrase": NAME}
    )
    rows = [r for r in data.get("rows", []) if r.get("name") == NAME]
    return rows[0] if rows else None


async def main() -> int:
    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)

    # Build the effective body exactly as the engine does: a library template body merged with
    # no override, validated by the engine's own validator.
    base_body = {
        "name": NAME,
        "type": "host",
        "content": WANT,
        "description": "OPNGMS template probe (delete me)",
    }
    eff = effective_body("firewall_alias", base_body, {})
    validate_alias_body(eff)

    rc = 1
    try:
        # Apply via the SAME connector write the engine's config-push path uses. Add an 'enabled'
        # flag like verify_live_push.py does (OPNsense expects it on addItem).
        add_payload = {"enabled": "1", **eff}
        add = await client.apply_alias("add", add_payload, dry_run=False)
        print(f"add        -> {add.get('result')}")
        row = await _row(client)
        present = row is not None
        # Read the content back. OPNsense returns alias content in the search row; confirm our
        # list values are all present (the row's content is typically a newline/comma string).
        content_str = "" if row is None else str(row.get("content", ""))
        content_ok = present and all(ip in content_str for ip in WANT)
        print(f"present    -> {present}")
        print(f"content_ok -> {content_ok}  (row content: {content_str!r})")
        rc = 0 if content_ok else 1
    finally:
        try:
            if await _row(client) is not None:
                await client.apply_alias("delete", {"name": NAME}, dry_run=False)
            gone = await _row(client) is None
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
