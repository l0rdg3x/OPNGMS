"""Render the superadmin log-fleet overview rows to CSV / HTML (for PDF). Pure, no I/O.

`rows` are the per-tenant dicts produced by `log_fleet.log_fleet_overview` (keys: tenant_name,
enabled, disabled, revoked, total_devices, last_log_at, volume). The `silent` column uses the same
rule as the live UI badge: enabled forwarding but no log within `stale_after`.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from html import escape

_COLUMNS = ["tenant_name", "enabled", "disabled", "revoked", "total_devices", "last_log_at", "volume", "silent"]


def _silent(row: dict, *, now: datetime, stale_after: timedelta) -> bool:
    if (row.get("enabled") or 0) <= 0:
        return False
    last = row.get("last_log_at")
    return last is None or (now - last) > stale_after


def _cells(row: dict, *, now: datetime, stale_after: timedelta) -> list:
    last = row.get("last_log_at")
    vol = row.get("volume")
    return [
        row.get("tenant_name", ""), row.get("enabled", 0), row.get("disabled", 0),
        row.get("revoked", 0), row.get("total_devices", 0),
        last.isoformat() if last else "", "" if vol is None else vol,
        "yes" if _silent(row, now=now, stale_after=stale_after) else "no",
    ]


def fleet_rows_to_csv(rows: list[dict], *, now: datetime, stale_after: timedelta) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_COLUMNS)
    for row in rows:
        writer.writerow(_cells(row, now=now, stale_after=stale_after))
    return buf.getvalue()


def fleet_rows_to_html(rows: list[dict], *, window: str, generated_at: datetime, now: datetime,
                       stale_after: timedelta) -> str:
    head = "".join(f"<th>{escape(c)}</th>" for c in _COLUMNS)
    body_rows = []
    for row in rows:
        cells = _cells(row, now=now, stale_after=stale_after)
        tds = "".join(f"<td>{escape(str(c))}</td>" for c in cells)
        body_rows.append(f"<tr>{tds}</tr>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'><style>"
        "body{font-family:sans-serif;font-size:11px} h1{font-size:16px}"
        "table{border-collapse:collapse;width:100%} th,td{border:1px solid #ccc;padding:4px;text-align:left}"
        "th{background:#f0f0f0}</style></head><body>"
        f"<h1>OPNGMS log fleet — volume window {escape(window)}</h1>"
        f"<p>Generated {escape(generated_at.isoformat())}</p>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"
        "</body></html>"
    )
