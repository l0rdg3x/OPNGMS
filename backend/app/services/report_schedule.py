"""Pure scheduling helpers for report delivery: cadence math + recipient validation.

No DB access — kept side-effect-free so the worker and the API share one source of truth and
the math is unit-testable in isolation.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from email_validator import EmailNotValidError, validate_email

WEEKLY = "weekly"
MONTHLY = "monthly"
ON_DEMAND = "on_demand"
FREQUENCIES = {WEEKLY, MONTHLY, ON_DEMAND}

MAX_RECIPIENTS = 50


def next_run_at(frequency: str, weekday: int | None, hour: int, *, after: datetime) -> datetime | None:
    """The next strictly-future fire time for a schedule, or None for on-demand.

    `weekday`: 0=Mon..6=Sun (required for WEEKLY). `hour`: 0..23 UTC. `after` is tz-aware UTC.
    """
    if frequency == ON_DEMAND:
        return None
    if frequency == WEEKLY:
        if weekday is None:
            raise ValueError("weekly schedule requires a weekday")
        candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
        days_ahead = (weekday - candidate.weekday()) % 7
        candidate = candidate + timedelta(days=days_ahead)
        if candidate <= after:
            candidate = candidate + timedelta(days=7)
        return candidate
    if frequency == MONTHLY:
        first_this = after.replace(day=1, hour=hour, minute=0, second=0, microsecond=0)
        if first_this > after:
            return first_this
        year, month = (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
        return after.replace(year=year, month=month, day=1, hour=hour, minute=0, second=0, microsecond=0)
    raise ValueError(f"unknown frequency: {frequency!r}")


def report_window(frequency: str, *, run_at: datetime) -> tuple[datetime, datetime]:
    """The (from, to) period a run covers. Weekly/on-demand = prior 7 days; monthly = prior month."""
    day_start = run_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if frequency == MONTHLY:
        to = day_start.replace(day=1)
        frm_year, frm_month = (to.year - 1, 12) if to.month == 1 else (to.year, to.month - 1)
        frm = to.replace(year=frm_year, month=frm_month)
        return frm, to
    to = day_start
    return to - timedelta(days=7), to


def normalize_recipients(raw: list[str]) -> list[str]:
    """Lower-case, trim, validate (email-validator), de-duplicate, and cap. Raises ValueError."""
    seen: list[str] = []
    for entry in raw:
        candidate = entry.strip()
        if not candidate:
            continue
        try:
            normalized = validate_email(candidate, check_deliverability=False).normalized.lower()
        except EmailNotValidError as exc:
            raise ValueError(f"invalid recipient {candidate!r}: {exc}") from exc
        if normalized not in seen:
            seen.append(normalized)
    if len(seen) > MAX_RECIPIENTS:
        raise ValueError(f"too many recipients (max {MAX_RECIPIENTS})")
    return seen
