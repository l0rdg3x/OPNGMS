#!/usr/bin/env python3
"""Live check of the generic opnsense_setting introspect+apply round-trip (NOT in CI). Introspects
ids/settings/get, flips ONE benign portable field (general.AlertSaveLogs) via apply_setting
(partial set + reconfigure), confirms via re-introspect, then reverts (guaranteed cleanup). Never
enables the IDS engine; credentials are never printed.

Usage: OPNSENSE_URL=https://192.168.1.82 OPNSENSE_KEYFILE=~/path/apikey.txt python scripts/verify_setting_live.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.connectors.opnsense.client import OpnsenseClient  # noqa: E402
from app.connectors.opnsense.setting_endpoints import SETTING_ENDPOINTS  # noqa: E402
from app.services.setting_introspect import infer_fields  # noqa: E402

# The field we flip — plain text/numeric, never enables/disables the IDS engine itself.
FIELD_PATH = "general.AlertSaveLogs"


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


def _field_value(fields: list[dict], path: str) -> str | list | None:
    """Return the current value for the given dotted path from an infer_fields result list."""
    for f in fields:
        if f["path"] == path:
            return f["value"]
    return None


async def main() -> int:
    base = os.environ["OPNSENSE_URL"]
    key, secret = _read_creds(os.environ["OPNSENSE_KEYFILE"])
    client = OpnsenseClient(base, key, secret, verify_tls=False)

    ep = SETTING_ENDPOINTS["ids_general"]

    # ── Step 1: introspect — read current value via get_setting + infer_fields ────────────────
    raw = await client.get_setting(ep.get_path)
    fields = infer_fields(raw, ep)
    original = _field_value(fields, FIELD_PATH)
    if original is None:
        # Fallback: read directly from the raw response tree if infer_fields skips it
        try:
            original = raw[ep.model_root]["general"]["AlertSaveLogs"]
        except (KeyError, TypeError):
            raise SystemExit(
                f"Could not find field {FIELD_PATH!r} in introspection response. "
                "The field may not exist on this firmware version."
            )
    print(f"introspect -> {FIELD_PATH} = {original!r}")

    # Also snapshot a DIFFERENT field to prove no-clobber after apply.
    # general.enabled is the IDS engine on/off switch — we only READ it, never change it.
    enabled_original = _field_value(fields, "general.enabled")
    if enabled_original is None:
        try:
            enabled_original = raw[ep.model_root]["general"]["enabled"]
        except (KeyError, TypeError):
            enabled_original = None
    print(f"introspect -> general.enabled = {enabled_original!r}  (must not change)")

    # ── Step 2: choose a benign new value (never "1" for enabled, just a log-count tweak) ────
    new = "5" if original != "5" else "6"
    print(f"plan       -> will flip {FIELD_PATH}: {original!r} -> {new!r}")

    rc = 1
    applied_ok = False
    confirmed_ok = False
    noclobber_ok = False
    reverted_ok = False

    try:
        # ── Step 3: apply partial set ────────────────────────────────────────────────────────
        res = await client.apply_setting(
            ep.set_path, ep.reconfigure_path, ep.model_root,
            {FIELD_PATH: new},
            dry_run=False,
        )
        applied_ok = True
        print(f"apply      -> {res}")

        # ── Step 4: confirm via re-introspect ────────────────────────────────────────────────
        raw2 = await client.get_setting(ep.get_path)
        fields2 = infer_fields(raw2, ep)
        read_back = _field_value(fields2, FIELD_PATH)
        if read_back is None:
            try:
                read_back = raw2[ep.model_root]["general"]["AlertSaveLogs"]
            except (KeyError, TypeError):
                read_back = None

        confirmed_ok = read_back == new
        print(f"confirm    -> {FIELD_PATH} = {read_back!r}  (expected {new!r})  ok={confirmed_ok}")

        # ── Step 5: no-clobber check — general.enabled must be unchanged ────────────────────
        enabled_after = _field_value(fields2, "general.enabled")
        if enabled_after is None:
            try:
                enabled_after = raw2[ep.model_root]["general"]["enabled"]
            except (KeyError, TypeError):
                enabled_after = None
        noclobber_ok = (enabled_original is None) or (enabled_after == enabled_original)
        print(
            f"no-clobber -> general.enabled = {enabled_after!r}  "
            f"(was {enabled_original!r})  ok={noclobber_ok}"
        )

        rc = 0 if (confirmed_ok and noclobber_ok) else 1

    finally:
        # ── Step 6: revert (guaranteed cleanup, even on failure) ─────────────────────────────
        try:
            rev = await client.apply_setting(
                ep.set_path, ep.reconfigure_path, ep.model_root,
                {FIELD_PATH: original},
                dry_run=False,
            )
            raw3 = await client.get_setting(ep.get_path)
            fields3 = infer_fields(raw3, ep)
            reverted_val = _field_value(fields3, FIELD_PATH)
            if reverted_val is None:
                try:
                    reverted_val = raw3[ep.model_root]["general"]["AlertSaveLogs"]
                except (KeyError, TypeError):
                    reverted_val = None
            reverted_ok = reverted_val == original
            print(
                f"revert     -> {FIELD_PATH} = {reverted_val!r}  "
                f"(expected {original!r})  ok={reverted_ok}"
            )
            if not reverted_ok:
                rc = 1
        except Exception as exc:  # noqa: BLE001
            print(f"REVERT ERROR: {type(exc).__name__}: {exc}")
            rc = 1

    print("ALL PASS" if rc == 0 else "FAILED")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
