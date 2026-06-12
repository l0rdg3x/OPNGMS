from datetime import UTC, datetime

import pytest

from app.services.report_schedule import (
    MAX_RECIPIENTS,
    MONTHLY,
    ON_DEMAND,
    WEEKLY,
    next_run_at,
    normalize_recipients,
    report_window,
)


def _dt(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=UTC)


def test_weekly_next_run_picks_chosen_weekday_in_future():
    after = _dt(2026, 6, 10, 9)
    assert next_run_at(WEEKLY, 0, 4, after=after) == _dt(2026, 6, 15, 4)


def test_weekly_today_but_hour_passed_rolls_a_week():
    after = _dt(2026, 6, 15, 9)
    assert next_run_at(WEEKLY, 0, 4, after=after) == _dt(2026, 6, 22, 4)


def test_weekly_today_hour_not_passed_is_today():
    after = _dt(2026, 6, 15, 2)
    assert next_run_at(WEEKLY, 0, 4, after=after) == _dt(2026, 6, 15, 4)


def test_monthly_next_run_is_first_of_next_month_when_past():
    after = _dt(2026, 6, 15, 9)
    assert next_run_at(MONTHLY, None, 4, after=after) == _dt(2026, 7, 1, 4)


def test_monthly_first_of_month_before_hour_is_today():
    after = _dt(2026, 6, 1, 2)
    assert next_run_at(MONTHLY, None, 4, after=after) == _dt(2026, 6, 1, 4)


def test_monthly_december_rolls_year():
    after = _dt(2026, 12, 20, 9)
    assert next_run_at(MONTHLY, None, 4, after=after) == _dt(2027, 1, 1, 4)


def test_on_demand_never_runs():
    assert next_run_at(ON_DEMAND, None, 4, after=_dt(2026, 6, 10)) is None


def test_window_weekly_is_prior_seven_days():
    frm, to = report_window(WEEKLY, run_at=_dt(2026, 6, 15, 4))
    assert to == _dt(2026, 6, 15)
    assert frm == _dt(2026, 6, 8)


def test_window_monthly_is_previous_calendar_month():
    frm, to = report_window(MONTHLY, run_at=_dt(2026, 6, 3, 4))
    assert to == _dt(2026, 6, 1)
    assert frm == _dt(2026, 5, 1)


def test_window_monthly_january_rolls_year():
    frm, to = report_window(MONTHLY, run_at=_dt(2026, 1, 3, 4))
    assert to == _dt(2026, 1, 1)
    assert frm == _dt(2025, 12, 1)


def test_normalize_recipients_dedupes_lowercases_validates():
    out = normalize_recipients([" A@X.io ", "a@x.io", "b@x.io"])
    assert out == ["a@x.io", "b@x.io"]


def test_normalize_recipients_rejects_bad_email():
    with pytest.raises(ValueError):
        normalize_recipients(["not-an-email"])


def test_normalize_recipients_caps_count():
    with pytest.raises(ValueError):
        normalize_recipients([f"u{i}@x.io" for i in range(MAX_RECIPIENTS + 1)])
