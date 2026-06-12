# Report Delivery & Scheduling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Email scheduled OPNsense PDF reports to per-tenant and per-device recipient lists via one superadmin-configured SMTP relay, on a weekly/monthly/on-demand cadence with send-retry.

**Architecture:** A global `smtp_settings` singleton (password encrypted at rest) feeds a transport-only `EmailService` (aiosmtplib). Tenant-scoped `report_schedule` rows (NULL device = fleet, set = device) drive an hourly worker cron that splits **generate+store** (`deliver_scheduled_report`) from **send+retry** (`send_report_email_job`), so a failing relay retries without regenerating the PDF. The existing `ReportService`/aggregator already accept a `device_id` filter, so per-device reports reuse the engine.

**Tech Stack:** Python 3.14 · FastAPI · SQLAlchemy 2 (async) · TimescaleDB + Postgres RLS · arq (Redis) · aiosmtplib · WeasyPrint · React 19 + Mantine v9 + openapi-fetch + TanStack Query · pytest · vitest + MSW.

**Spec:** `docs/superpowers/specs/2026-06-12-report-delivery-design.md`

**Branch:** `feat/report-delivery` (already created off `main`).

---

## File Structure

**Backend — create:**
- `app/models/smtp_settings.py` — `SmtpSettings` singleton model (id=1 guard, encrypted password).
- `app/models/report_schedule.py` — `ReportSchedule` model (tenant_id + nullable device_id).
- `app/services/email/__init__.py`, `app/services/email/smtp.py` — `EmailService` transport (aiosmtplib).
- `app/services/smtp_settings.py` — `SmtpSettingsService` (get/upsert/encrypt + `to_send_config`).
- `app/services/report_schedule.py` — pure scheduling helpers (`next_run_at`, `report_window`, `normalize_recipients`, frequency constants).
- `app/repositories/report_schedule.py` — `ReportScheduleRepository` (CRUD + next_run_at maintenance).
- `app/schemas/smtp.py` — `SmtpSettingsIn/Out`, `SmtpTestIn/Out`.
- `app/schemas/report_schedule.py` — `ReportScheduleIn/Out`.
- `app/api/smtp.py` — superadmin SMTP config + test endpoints.
- `app/api/report_schedules.py` — tenant schedule CRUD + send-now.
- `migrations/versions/0022_report_delivery.py` — both tables + 2 columns + RLS for `report_schedule`.

**Backend — modify:**
- `app/core/rls.py` — add `report_schedule` to `TENANT_TABLES`.
- `app/models/__init__.py` — import the two new models so `Base.metadata` sees them.
- `app/models/report_settings.py` — add `from_email`.
- `app/models/generated_report.py` — add `device_id`.
- `app/repositories/report_settings.py` — thread `from_email` through `upsert`/`get_or_default`.
- `app/repositories/generated_report.py` — `create(..., device_id=None)`.
- `app/schemas/report_settings.py` — add `from_email`.
- `app/api/reports.py` — surface `from_email` in `_settings_to_out` + `update_report_settings`.
- `app/services/reporting/aggregation.py` — add `ReportAggregator.device(device_id)`.
- `app/services/reporting/context.py` — `build_context(..., device_id=None)`.
- `app/services/reporting/service.py` — `build_report(..., device_id=None)`.
- `app/main.py` — include the two new routers.
- `app/worker.py` — replace weekly cron with `enqueue_due_reports` + `deliver_scheduled_report` + `send_report_email_job`; update `WorkerSettings`.
- `pyproject.toml` — add `aiosmtplib`.

**Frontend — create:**
- `frontend/src/admin/smtpHooks.ts`, `frontend/src/pages/SmtpSettingsPage.tsx`.
- `frontend/src/reports/scheduleHooks.ts`, `frontend/src/pages/ReportSchedulePage.tsx`.

**Frontend — modify:**
- `frontend/src/api/schema.d.ts` (regenerated via `npm run gen:api`).
- `frontend/src/components/AppShell.tsx` — routes + nav (superadmin gate for SMTP).
- `frontend/src/reports/settingsHooks.ts`, `frontend/src/pages/ReportSettingsPage.tsx` — `from_email` field.
- `frontend/src/i18n/en.ts` — new strings.

---

## Conventions (read once)

- **Run tests:** from `backend/`: `.venv/bin/pytest tests/<file>::<test> -v`. The suite needs Postgres+Timescale and `TEST_DATABASE_URL` (the env sets `opngms_test`); DB-backed tests `skip` without it. Pure-logic tests (no `db_engine` fixture) always run.
- **Frontend tests:** from `frontend/`: `npm test -- <file>`. Before any frontend PR run the full **`npm run build`** (tsc -b + vite) — `tsc -b` type-checks tests too.
- **Auth in API tests:** see `tests/test_mfa_admin_api.py` — `make_user(s, email=…, is_superadmin=True)`, `POST /api/login`, then `csrf_headers(client)` on writes. Fixtures: `api_client` (owner session) + `db_engine`.
- **Commit** after each task's tests pass. English everywhere in code/commits.

---

# PHASE A — SMTP + tenant delivery

## Task A1: Add the aiosmtplib dependency

**Files:**
- Modify: `backend/pyproject.toml`

- [ ] **Step 1: Add the dependency**

In `backend/pyproject.toml`, add to the `dependencies` array (after `"email-validator>=2.3.0",`):

```toml
    "aiosmtplib>=4.0",
```

- [ ] **Step 2: Install**

Run: `cd backend && .venv/bin/pip install 'aiosmtplib>=4.0'`
Expected: installs aiosmtplib (and confirms it imports).

- [ ] **Step 3: Verify import**

Run: `cd backend && .venv/bin/python -c "import aiosmtplib; print(aiosmtplib.__version__)"`
Expected: prints a 4.x version.

- [ ] **Step 4: Commit**

```bash
git add backend/pyproject.toml
git commit -m "build(reports): add aiosmtplib dependency"
```

---

## Task A2: Scheduling helpers (pure functions)

The cadence math and recipient validation are pure (no DB) → test first, in isolation.

**Files:**
- Create: `backend/app/services/report_schedule.py`
- Test: `backend/tests/test_report_schedule_helpers.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_report_schedule_helpers.py
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
    # Wed 2026-06-10 09:00; want Monday(0) at 04:00 -> next Monday 2026-06-15 04:00
    after = _dt(2026, 6, 10, 9)
    assert next_run_at(WEEKLY, 0, 4, after=after) == _dt(2026, 6, 15, 4)


def test_weekly_today_but_hour_passed_rolls_a_week():
    # Mon 2026-06-15 09:00; weekday Monday(0) hour 4 already passed -> +7 days
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
    frm, to = report_window(WEEKLY, run_at=_dt(2026, 6, 15, 4, ))
    assert to == _dt(2026, 6, 15)        # run-day start
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedule_helpers.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.report_schedule`.

- [ ] **Step 3: Implement the helpers**

```python
# backend/app/services/report_schedule.py
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
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedule_helpers.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/report_schedule.py backend/tests/test_report_schedule_helpers.py
git commit -m "feat(reports): scheduling cadence + recipient helpers"
```

---

## Task A3: EmailService transport (aiosmtplib)

Transport only — builds a MIME message and sends it. No reports/tenants knowledge.

**Files:**
- Create: `backend/app/services/email/__init__.py` (empty), `backend/app/services/email/smtp.py`
- Test: `backend/tests/test_email_smtp.py`

- [ ] **Step 1: Write the failing tests** (monkeypatch `aiosmtplib.send` — no live SMTP)

```python
# backend/tests/test_email_smtp.py
import aiosmtplib
import pytest

from app.services.email.smtp import EmailSendError, SmtpSendConfig, send_report_email


def _cfg(**over):
    base = dict(host="smtp.x.io", port=587, security="starttls", username="u",
                password="p", from_email="noc@x.io", from_name="OPNGMS NOC")
    base.update(over)
    return SmtpSendConfig(**base)


async def test_send_builds_message_with_pdf_attachment(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        captured["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(),
        subject="Weekly report",
        recipients=["a@x.io", "b@x.io"],
        body_text="Attached.",
        attachment=("report.pdf", b"%PDF-1.4 fake", "application/pdf"),
    )
    msg = captured["message"]
    assert msg["Subject"] == "Weekly report"
    assert msg["From"] == "OPNGMS NOC <noc@x.io>"
    assert msg["To"] == "a@x.io, b@x.io"
    # starttls -> start_tls True, not implicit TLS
    assert captured["kwargs"]["hostname"] == "smtp.x.io"
    assert captured["kwargs"]["port"] == 587
    assert captured["kwargs"]["start_tls"] is True
    assert captured["kwargs"].get("use_tls") in (False, None)
    assert captured["kwargs"]["username"] == "u"
    # PDF attachment present
    parts = [p.get_filename() for p in msg.iter_attachments()]
    assert parts == ["report.pdf"]


async def test_implicit_tls_mode_sets_use_tls(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(security="tls", port=465),
        subject="s", recipients=["a@x.io"], body_text="b",
        attachment=("r.pdf", b"%PDF-", "application/pdf"),
    )
    assert captured["kwargs"]["use_tls"] is True
    assert captured["kwargs"].get("start_tls") in (False, None)


async def test_no_username_skips_auth(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["kwargs"] = kwargs
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(username=None, password=None),
        subject="s", recipients=["a@x.io"], body_text="b",
        attachment=("r.pdf", b"%PDF-", "application/pdf"),
    )
    assert captured["kwargs"].get("username") is None


async def test_transport_failure_wrapped(monkeypatch):
    async def boom(message, **kwargs):
        raise aiosmtplib.SMTPException("connection refused")

    monkeypatch.setattr(aiosmtplib, "send", boom)
    with pytest.raises(EmailSendError):
        await send_report_email(
            _cfg(), subject="s", recipients=["a@x.io"], body_text="b",
            attachment=("r.pdf", b"%PDF-", "application/pdf"),
        )


async def test_header_injection_stripped(monkeypatch):
    captured = {}

    async def fake_send(message, **kwargs):
        captured["message"] = message
        return ({}, "ok")

    monkeypatch.setattr(aiosmtplib, "send", fake_send)
    await send_report_email(
        _cfg(from_name="Evil\r\nBcc: victim@x.io"),
        subject="s\r\nX-Injected: 1", recipients=["a@x.io"], body_text="b",
        attachment=("r.pdf", b"%PDF-", "application/pdf"),
    )
    msg = captured["message"]
    assert "\n" not in msg["Subject"] and "\r" not in msg["Subject"]
    assert "Bcc" not in msg
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_email_smtp.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.email.smtp`.

- [ ] **Step 3: Implement**

Create empty `backend/app/services/email/__init__.py`. Then:

```python
# backend/app/services/email/smtp.py
"""SMTP transport for report delivery. Pure transport: build a MIME message and send it.

Knows nothing about reports/tenants — callers pass a resolved config + message parts.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.message import EmailMessage

import aiosmtplib


class EmailSendError(Exception):
    """Wraps any SMTP transport/auth failure so callers catch one type."""


@dataclass
class SmtpSendConfig:
    host: str
    port: int
    security: str  # "starttls" | "tls" | "none"
    username: str | None
    password: str | None
    from_email: str
    from_name: str


def _strip(value: str) -> str:
    # Defuse header injection: no CR/LF in any header-derived value.
    return value.replace("\r", " ").replace("\n", " ").strip()


def _build_message(
    cfg: SmtpSendConfig,
    *,
    subject: str,
    recipients: list[str],
    body_text: str,
    attachment: tuple[str, bytes, str],
) -> EmailMessage:
    msg = EmailMessage()
    from_name = _strip(cfg.from_name)
    msg["From"] = f"{from_name} <{cfg.from_email}>" if from_name else cfg.from_email
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = _strip(subject)
    msg.set_content(body_text)
    filename, data, mime = attachment
    maintype, _, subtype = mime.partition("/")
    msg.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=filename)
    return msg


async def send_report_email(
    cfg: SmtpSendConfig,
    *,
    subject: str,
    recipients: list[str],
    body_text: str,
    attachment: tuple[str, bytes, str],
) -> None:
    """Send one email with a single attachment. Raises EmailSendError on any failure."""
    message = _build_message(
        cfg, subject=subject, recipients=recipients, body_text=body_text, attachment=attachment
    )
    kwargs: dict = {
        "hostname": cfg.host,
        "port": cfg.port,
        "start_tls": cfg.security == "starttls",
        "use_tls": cfg.security == "tls",
    }
    if cfg.username:
        kwargs["username"] = cfg.username
        kwargs["password"] = cfg.password or ""
    try:
        await aiosmtplib.send(message, **kwargs)
    except (aiosmtplib.SMTPException, OSError) as exc:
        raise EmailSendError(str(exc)) from exc
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_email_smtp.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/email/ backend/tests/test_email_smtp.py
git commit -m "feat(reports): SMTP email transport (aiosmtplib)"
```

---

## Task A4: SmtpSettings model + register

**Files:**
- Create: `backend/app/models/smtp_settings.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_smtp_settings_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_smtp_settings_model.py
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.smtp_settings import SINGLETON_ID, SmtpSettings


async def test_smtp_settings_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        s.add(SmtpSettings(
            id=SINGLETON_ID, enabled=True, host="smtp.x.io", port=587, security="starttls",
            username="u", password_enc=b"enc", from_email="noc@x.io", from_name="NOC",
        ))
        await s.commit()
    async with factory() as s:
        row = (await s.execute(select(SmtpSettings))).scalar_one()
        assert row.id == SINGLETON_ID
        assert row.host == "smtp.x.io"
        assert row.enabled is True
        assert row.password_enc == b"enc"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_smtp_settings_model.py -v`
Expected: FAIL (`ModuleNotFoundError`). (If `TEST_DATABASE_URL` unset it skips — set it to run.)

- [ ] **Step 3: Implement the model**

```python
# backend/app/models/smtp_settings.py
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, LargeBinary, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

SINGLETON_ID = 1


class SmtpSettings(Base):
    """Global (non-tenant) SMTP relay config — a single row (id=1). Password encrypted at rest.

    Not tenant-scoped: only the owner-connected worker and superadmin-gated API touch it, so no RLS.
    """

    __tablename__ = "smtp_settings"
    __table_args__ = (CheckConstraint("id = 1", name="ck_smtp_settings_singleton"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(default=False, server_default="false")
    host: Mapped[str] = mapped_column(String, default="", server_default="")
    port: Mapped[int] = mapped_column(Integer, default=587, server_default="587")
    security: Mapped[str] = mapped_column(String, default="starttls", server_default="starttls")
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    password_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    from_email: Mapped[str] = mapped_column(String, default="", server_default="")
    from_name: Mapped[str] = mapped_column(String, default="", server_default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Register the model**

In `backend/app/models/__init__.py`, add an import so `Base.metadata.create_all` (used by the test conftest) sees the table. Match the file's existing import style; add:

```python
from app.models.smtp_settings import SmtpSettings  # noqa: F401
```

(Check the file first — if it uses an `__all__`, append `"SmtpSettings"` to it too.)

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_smtp_settings_model.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/smtp_settings.py backend/app/models/__init__.py backend/tests/test_smtp_settings_model.py
git commit -m "feat(reports): SmtpSettings singleton model"
```

---

## Task A5: ReportSchedule model + RLS registration

**Files:**
- Create: `backend/app/models/report_schedule.py`
- Modify: `backend/app/models/__init__.py`, `backend/app/core/rls.py`
- Test: `backend/tests/test_report_schedule_model.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_schedule_model.py
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.report_schedule import ReportSchedule


async def _tenant(s):
    tid = uuid.uuid4()
    await s.execute(
        text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'A', 'a', 'active')"),
        {"id": tid},
    )
    return tid


async def test_report_schedule_roundtrip(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid = await _tenant(s)
        s.add(ReportSchedule(
            tenant_id=tid, device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
            recipients=["a@x.io"], next_run_at=datetime(2026, 6, 15, 4, tzinfo=UTC),
        ))
        await s.commit()
        row = (await s.execute(select(ReportSchedule))).scalar_one()
        assert row.frequency == "weekly"
        assert row.recipients == ["a@x.io"]
        assert row.device_id is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedule_model.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement the model**

```python
# backend/app/models/report_schedule.py
import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY, Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class ReportSchedule(UUIDPKMixin, Base):
    """A report delivery schedule. device_id NULL = tenant/fleet scope; set = that device."""

    __tablename__ = "report_schedule"
    __table_args__ = (
        CheckConstraint("hour BETWEEN 0 AND 23", name="ck_report_schedule_hour"),
        CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="ck_report_schedule_weekday"),
        Index(
            "uq_report_schedule_tenant", "tenant_id",
            unique=True, postgresql_where=text("device_id IS NULL"),
        ),
        Index(
            "uq_report_schedule_device", "tenant_id", "device_id",
            unique=True, postgresql_where=text("device_id IS NOT NULL"),
        ),
        Index("ix_report_schedule_due", "enabled", "next_run_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    frequency: Mapped[str] = mapped_column(String)  # weekly | monthly | on_demand
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0=Mon..6=Sun (weekly)
    hour: Mapped[int] = mapped_column(Integer, default=4, server_default="4")
    recipients: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

- [ ] **Step 4: Register the model + RLS table**

In `backend/app/models/__init__.py` add:

```python
from app.models.report_schedule import ReportSchedule  # noqa: F401
```

In `backend/app/core/rls.py`, add `"report_schedule"` to the `TENANT_TABLES` list (end of the list):

```python
TENANT_TABLES: list[str] = ["devices", "metrics", "alerts", "events", "config_snapshots", "config_changes", "report_settings", "generated_reports", "firmware_actions", "template_overrides", "report_schedule"]
```

- [ ] **Step 5: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedule_model.py -v`
Expected: PASS (the conftest's `enable_rls_statements()` now also enables RLS on `report_schedule`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/report_schedule.py backend/app/models/__init__.py backend/app/core/rls.py backend/tests/test_report_schedule_model.py
git commit -m "feat(reports): ReportSchedule model + RLS registration"
```

---

## Task A6: report_settings.from_email + generated_reports.device_id (models + repos)

**Files:**
- Modify: `backend/app/models/report_settings.py`, `backend/app/models/generated_report.py`
- Modify: `backend/app/repositories/report_settings.py`, `backend/app/repositories/generated_report.py`
- Test: `backend/tests/test_report_settings_model.py` (extend), `backend/tests/test_generated_report_model.py` (extend)

- [ ] **Step 1: Write failing assertions**

Append to `backend/tests/test_report_settings_model.py`:

```python
async def test_report_settings_from_email_default_and_set(db_engine):
    import uuid
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.repositories.report_settings import ReportSettingsRepository

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid = uuid.uuid4()
    async with factory() as s:
        await s.execute(
            text("INSERT INTO tenants (id, name, slug, status) VALUES (:id, 'A', 'a', 'active')"),
            {"id": tid},
        )
        repo = ReportSettingsRepository(s, tid)
        row = await repo.upsert(title="T", owner="o", timezone="UTC", language="en",
                                from_email="brand@x.io")
        assert row.from_email == "brand@x.io"
        default = await ReportSettingsRepository(s, uuid.uuid4()).get_or_default()
        assert default.from_email == ""
```

Append to `backend/tests/test_generated_report_model.py`:

```python
async def test_generated_report_stores_device_id(db_engine):
    import uuid
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.repositories.generated_report import GeneratedReportRepository

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id, name, slug, status) VALUES (:id,'A','a','active')"), {"id": tid})
        await s.execute(text(
            "INSERT INTO devices (id, tenant_id, name, base_url, api_key_enc, api_secret_enc, verify_tls, status, tags) "
            "VALUES (:id,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"id": did, "t": tid})
        row = await GeneratedReportRepository(s, tid).create(
            kind="scheduled", period_from=__import__("datetime").datetime(2026,6,1,tzinfo=__import__("datetime").timezone.utc),
            period_to=__import__("datetime").datetime(2026,6,8,tzinfo=__import__("datetime").timezone.utc),
            created_by=None, pdf=b"%PDF-", device_id=did)
        assert row.device_id == did
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_report_settings_model.py::test_report_settings_from_email_default_and_set tests/test_generated_report_model.py::test_generated_report_stores_device_id -v`
Expected: FAIL (`from_email`/`device_id` unknown).

- [ ] **Step 3: Implement model + repo changes**

`backend/app/models/report_settings.py` — add after `language`:

```python
    from_email: Mapped[str] = mapped_column(String, default="", server_default="")
```

`backend/app/repositories/report_settings.py` — update `get_or_default` to include `from_email=""` in the transient object, and change `upsert` signature + body:

```python
    async def upsert(self, *, title: str, owner: str, timezone: str, language: str = "en",
                     from_email: str = "") -> ReportSettings:
        row = await self.get()
        if row is None:
            row = ReportSettings(tenant_id=self.tenant_id)
            self.session.add(row)
        row.title, row.owner, row.timezone = title, owner, timezone
        row.language = language
        row.from_email = from_email
        await self.session.flush()
        return row
```

In `get_or_default`'s transient return, add `from_email=""`:

```python
        return ReportSettings(
            tenant_id=self.tenant_id, title="Security & Activity Report", owner="",
            timezone="UTC", language="en", from_email="",
        )
```

`backend/app/models/generated_report.py` — add a `device_id` column (after `tenant_id`):

```python
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
```

`backend/app/repositories/generated_report.py` — extend `create`:

```python
    async def create(self, *, kind: str, period_from: datetime, period_to: datetime,
                     created_by: uuid.UUID | None, pdf: bytes,
                     device_id: uuid.UUID | None = None) -> GeneratedReport:
        row = GeneratedReport(
            tenant_id=self.tenant_id, device_id=device_id, kind=kind, period_from=period_from,
            period_to=period_to, created_by=created_by, pdf=pdf, size=len(pdf),
        )
        self.session.add(row)
        await self.session.flush()
        return row
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_settings_model.py tests/test_generated_report_model.py -v`
Expected: PASS (existing tests still pass + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/report_settings.py backend/app/models/generated_report.py backend/app/repositories/report_settings.py backend/app/repositories/generated_report.py backend/tests/test_report_settings_model.py backend/tests/test_generated_report_model.py
git commit -m "feat(reports): report_settings.from_email + generated_reports.device_id"
```

---

## Task A7: Migration 0022 (tables + columns + RLS)

**Files:**
- Create: `backend/migrations/versions/0022_report_delivery.py`
- Test: `backend/tests/test_migration_0022.py`

- [ ] **Step 1: Write the failing migration test** (mirror `tests/test_migration_0020.py` — read it first for the alembic-run harness)

```python
# backend/tests/test_migration_0022.py
"""0022 creates smtp_settings + report_schedule (+RLS) and adds report_settings.from_email
and generated_reports.device_id."""
import os

import pytest
from sqlalchemy import text

from alembic import command
from alembic.config import Config

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="needs DB")


def _alembic_cfg():
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", os.environ["TEST_DATABASE_URL"].replace("+asyncpg", ""))
    return cfg


async def test_migration_0022_schema(db_engine):
    # db_engine already built the head schema via metadata; assert the new objects exist.
    async with db_engine.begin() as conn:
        cols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='report_settings'"
        ))).scalars().all()
        assert "from_email" in cols
        gcols = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='generated_reports'"
        ))).scalars().all()
        assert "device_id" in gcols
        tabs = (await conn.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_name IN ('smtp_settings','report_schedule')"
        ))).scalars().all()
        assert set(tabs) == {"smtp_settings", "report_schedule"}
        # report_schedule has RLS forced
        rls = (await conn.execute(text(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname='report_schedule'"
        ))).one()
        assert rls == (True, True)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_migration_0022.py -v`
Expected: FAIL (the assertions fail or the migration import is missing) — actually since `db_engine` builds from metadata (not alembic), `from_email`/`device_id`/tables already exist from Tasks A4–A6; the RLS-forced assertion on `report_schedule` passes via conftest. This test mainly guards that the **migration file** can run on a real DB. Proceed to write the migration and verify it applies cleanly in Step 4.

- [ ] **Step 3: Write the migration**

```python
# backend/migrations/versions/0022_report_delivery.py
"""report delivery: smtp_settings + report_schedule (+RLS) + report_settings.from_email
and generated_reports.device_id"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.core.db_roles import APP_ROLE, grant_app_role_statements
from app.core.rls import POLICY_NAME, policy_create_statement

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- global SMTP singleton (no RLS; owner + superadmin API only) ---
    op.create_table(
        "smtp_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("host", sa.String(), nullable=False, server_default=""),
        sa.Column("port", sa.Integer(), nullable=False, server_default="587"),
        sa.Column("security", sa.String(), nullable=False, server_default="starttls"),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("password_enc", sa.LargeBinary(), nullable=True),
        sa.Column("from_email", sa.String(), nullable=False, server_default=""),
        sa.Column("from_name", sa.String(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_smtp_settings_singleton"),
    )

    # --- tenant-scoped schedules (RLS) ---
    op.create_table(
        "report_schedule",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("frequency", sa.String(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("hour", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("recipients", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name="ck_report_schedule_hour"),
        sa.CheckConstraint("weekday IS NULL OR weekday BETWEEN 0 AND 6", name="ck_report_schedule_weekday"),
    )
    op.create_index("uq_report_schedule_tenant", "report_schedule", ["tenant_id"], unique=True,
                    postgresql_where=sa.text("device_id IS NULL"))
    op.create_index("uq_report_schedule_device", "report_schedule", ["tenant_id", "device_id"], unique=True,
                    postgresql_where=sa.text("device_id IS NOT NULL"))
    op.create_index("ix_report_schedule_due", "report_schedule", ["enabled", "next_run_at"])
    op.execute("ALTER TABLE report_schedule ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_schedule FORCE ROW LEVEL SECURITY")
    op.execute(policy_create_statement("report_schedule"))

    # --- column adds ---
    op.add_column("report_settings", sa.Column("from_email", sa.String(), nullable=False, server_default=""))
    op.add_column("generated_reports", sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_generated_reports_device", "generated_reports", "devices",
                          ["device_id"], ["id"], ondelete="SET NULL")

    for stmt in grant_app_role_statements():
        op.execute(stmt)


def downgrade() -> None:
    op.drop_constraint("fk_generated_reports_device", "generated_reports", type_="foreignkey")
    op.drop_column("generated_reports", "device_id")
    op.drop_column("report_settings", "from_email")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON report_schedule FROM {APP_ROLE}")
    op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON report_schedule")
    op.execute("ALTER TABLE report_schedule NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_schedule DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_report_schedule_due", table_name="report_schedule")
    op.drop_index("uq_report_schedule_device", table_name="report_schedule")
    op.drop_index("uq_report_schedule_tenant", table_name="report_schedule")
    op.drop_table("report_schedule")
    op.drop_table("smtp_settings")
```

- [ ] **Step 4: Verify the migration applies on a scratch DB**

Run (creates a throwaway DB, migrates to head, then drops it):

```bash
cd backend && .venv/bin/python - <<'PY'
import asyncio, os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

ADMIN = os.environ["ADMIN_DATABASE_URL"]  # owner
async def main():
    eng = create_async_engine(ADMIN.rsplit("/",1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    async with eng.connect() as c:
        await c.execute(text("DROP DATABASE IF EXISTS opngms_mig0022"))
        await c.execute(text("CREATE DATABASE opngms_mig0022"))
    await eng.dispose()
asyncio.run(main())
PY
ALEMBIC_DATABASE_URL="$(echo $ADMIN_DATABASE_URL | sed 's#/[^/]*$#/opngms_mig0022#; s/+asyncpg//')" .venv/bin/alembic upgrade head
# then downgrade one + back to confirm reversibility
ALEMBIC_DATABASE_URL="$(echo $ADMIN_DATABASE_URL | sed 's#/[^/]*$#/opngms_mig0022#; s/+asyncpg//')" .venv/bin/alembic downgrade -1
ALEMBIC_DATABASE_URL="$(echo $ADMIN_DATABASE_URL | sed 's#/[^/]*$#/opngms_mig0022#; s/+asyncpg//')" .venv/bin/alembic upgrade head
```

Expected: `Running upgrade 0021 -> 0022` then a clean downgrade/upgrade. Drop the scratch DB afterwards (`DROP DATABASE opngms_mig0022`). Then run the test:

Run: `cd backend && .venv/bin/pytest tests/test_migration_0022.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/migrations/versions/0022_report_delivery.py backend/tests/test_migration_0022.py
git commit -m "feat(reports): migration 0022 — smtp_settings, report_schedule, new columns"
```

---

## Task A8: SmtpSettingsService

**Files:**
- Create: `backend/app/services/smtp_settings.py`
- Test: `backend/tests/test_smtp_settings_service.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_smtp_settings_service.py
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.smtp_settings import SmtpSettingsService


async def test_upsert_encrypts_password_and_to_send_config(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        row = await svc.upsert(enabled=True, host="smtp.x.io", port=587, security="starttls",
                               username="u", from_email="noc@x.io", from_name="NOC",
                               password="secret", clear_password=False)
        await s.commit()
        assert row.password_enc is not None
        assert b"secret" not in row.password_enc  # encrypted, not plaintext
        cfg = svc.to_send_config(row)
        assert cfg.password == "secret"
        assert cfg.host == "smtp.x.io"


async def test_upsert_keeps_password_when_omitted(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        await svc.upsert(enabled=True, host="h", port=587, security="starttls", username="u",
                         from_email="n@x.io", from_name="N", password="keepme", clear_password=False)
        await s.commit()
        row = await svc.upsert(enabled=True, host="h2", port=25, security="none", username="u",
                               from_email="n@x.io", from_name="N", password=None, clear_password=False)
        await s.commit()
        assert svc.to_send_config(row).password == "keepme"  # preserved
        assert row.host == "h2"


async def test_clear_password(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        svc = SmtpSettingsService(s)
        await svc.upsert(enabled=True, host="h", port=587, security="starttls", username="u",
                         from_email="n@x.io", from_name="N", password="x", clear_password=False)
        await s.commit()
        row = await svc.upsert(enabled=True, host="h", port=587, security="none", username=None,
                               from_email="n@x.io", from_name="N", password=None, clear_password=True)
        await s.commit()
        assert row.password_enc is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_smtp_settings_service.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# backend/app/services/smtp_settings.py
"""Read/write the global SMTP singleton, encrypting the password at rest (Fernet)."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.models.smtp_settings import SINGLETON_ID, SmtpSettings
from app.services.email.smtp import SmtpSendConfig


class SmtpSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> SmtpSettings | None:
        return (await self.session.execute(select(SmtpSettings))).scalar_one_or_none()

    async def upsert(self, *, enabled: bool, host: str, port: int, security: str,
                     username: str | None, from_email: str, from_name: str,
                     password: str | None, clear_password: bool) -> SmtpSettings:
        row = await self.get()
        if row is None:
            row = SmtpSettings(id=SINGLETON_ID)
            self.session.add(row)
        row.enabled, row.host, row.port, row.security = enabled, host, port, security
        row.username = username or None
        row.from_email, row.from_name = from_email, from_name
        if clear_password:
            row.password_enc = None
        elif password:
            row.password_enc = crypto.encrypt(password)
        # password is None and not clear_password -> keep existing
        await self.session.flush()
        return row

    def to_send_config(self, row: SmtpSettings) -> SmtpSendConfig:
        return SmtpSendConfig(
            host=row.host, port=row.port, security=row.security, username=row.username,
            password=crypto.decrypt(row.password_enc) if row.password_enc else None,
            from_email=row.from_email, from_name=row.from_name,
        )
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_smtp_settings_service.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/smtp_settings.py backend/tests/test_smtp_settings_service.py
git commit -m "feat(reports): SmtpSettingsService (encrypt + send-config)"
```

---

## Task A9: SMTP schemas + superadmin API

**Files:**
- Create: `backend/app/schemas/smtp.py`, `backend/app/api/smtp.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_smtp_api.py`

- [ ] **Step 1: Write the failing test** (mirror `tests/test_mfa_admin_api.py` auth)

```python
# backend/tests/test_smtp_api.py
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.conftest import csrf_headers
from tests.factories import make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        await make_user(s, email="sa@x.io", password="pw12345", is_superadmin=True)
        await make_user(s, email="reg@x.io", password="pw12345")
        await s.commit()


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200, r.text


async def test_get_hides_password_and_put_roundtrips(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    g = await api_client.get("/api/admin/smtp")
    assert g.status_code == 200
    assert g.json()["has_password"] is False
    assert "password" not in g.json()
    p = await api_client.put("/api/admin/smtp", headers=csrf_headers(api_client), json={
        "enabled": True, "host": "smtp.x.io", "port": 587, "security": "starttls",
        "username": "u", "from_email": "noc@x.io", "from_name": "NOC", "password": "secret",
    })
    assert p.status_code == 200, p.text
    g2 = await api_client.get("/api/admin/smtp")
    assert g2.json()["host"] == "smtp.x.io"
    assert g2.json()["has_password"] is True
    assert "password" not in g2.json()  # never returned


async def test_non_superadmin_denied(api_client, db_engine):
    await _seed(db_engine)
    await _login(api_client, "reg@x.io")
    assert (await api_client.get("/api/admin/smtp")).status_code == 403


async def test_smtp_test_uses_submitted_config(api_client, db_engine, monkeypatch):
    import app.api.smtp as smtp_api

    sent = {}

    async def fake_send(cfg, **kw):
        sent["cfg"] = cfg
        sent["recipients"] = kw["recipients"]

    monkeypatch.setattr(smtp_api, "send_report_email", fake_send)
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post("/api/admin/smtp/test", headers=csrf_headers(api_client), json={
        "to": "ops@x.io", "host": "smtp.x.io", "port": 587, "security": "starttls",
        "username": "u", "from_email": "noc@x.io", "from_name": "NOC", "password": "secret",
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert sent["recipients"] == ["ops@x.io"]


async def test_smtp_test_reports_failure(api_client, db_engine, monkeypatch):
    import app.api.smtp as smtp_api
    from app.services.email.smtp import EmailSendError

    async def boom(cfg, **kw):
        raise EmailSendError("auth failed")

    monkeypatch.setattr(smtp_api, "send_report_email", boom)
    await _seed(db_engine)
    await _login(api_client, "sa@x.io")
    r = await api_client.post("/api/admin/smtp/test", headers=csrf_headers(api_client), json={
        "to": "ops@x.io", "host": "h", "port": 587, "security": "starttls",
        "username": None, "from_email": "noc@x.io", "from_name": "NOC", "password": None,
    })
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "auth failed" in r.json()["detail"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_smtp_api.py -v`
Expected: FAIL (router not mounted / module missing).

- [ ] **Step 3: Implement the schemas**

```python
# backend/app/schemas/smtp.py
from pydantic import BaseModel, EmailStr, Field

SECURITIES = {"starttls", "tls", "none"}


class SmtpSettingsIn(BaseModel):
    enabled: bool = False
    host: str = Field(max_length=255)
    port: int = Field(ge=1, le=65535)
    security: str = "starttls"
    username: str | None = Field(default=None, max_length=255)
    from_email: EmailStr
    from_name: str = Field(default="", max_length=255)
    password: str | None = Field(default=None, max_length=1024)  # None=keep, ""=clear
    clear_password: bool = False


class SmtpSettingsOut(BaseModel):
    enabled: bool
    host: str
    port: int
    security: str
    username: str | None
    from_email: str
    from_name: str
    has_password: bool


class SmtpTestIn(SmtpSettingsIn):
    to: EmailStr


class SmtpTestOut(BaseModel):
    ok: bool
    detail: str = ""
```

- [ ] **Step 4: Implement the API**

```python
# backend/app/api/smtp.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import enforce_csrf, require_org
from app.core.rbac import Action
from app.models.user import User
from app.schemas.smtp import SECURITIES, SmtpSettingsIn, SmtpSettingsOut, SmtpTestIn, SmtpTestOut
from app.services.audit import AuditService
from app.services.email.smtp import EmailSendError, SmtpSendConfig, send_report_email
from app.services.smtp_settings import SmtpSettingsService

router = APIRouter(prefix="/api/admin/smtp", tags=["smtp"])


def _out(row) -> SmtpSettingsOut:
    if row is None:
        return SmtpSettingsOut(enabled=False, host="", port=587, security="starttls",
                               username=None, from_email="", from_name="", has_password=False)
    return SmtpSettingsOut(
        enabled=row.enabled, host=row.host, port=row.port, security=row.security,
        username=row.username, from_email=row.from_email, from_name=row.from_name,
        has_password=row.password_enc is not None,
    )


@router.get("", response_model=SmtpSettingsOut)
async def get_smtp(
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> SmtpSettingsOut:
    return _out(await SmtpSettingsService(session).get())


@router.put("", response_model=SmtpSettingsOut, dependencies=[Depends(enforce_csrf)])
async def put_smtp(
    body: SmtpSettingsIn,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> SmtpSettingsOut:
    if body.security not in SECURITIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid security")
    svc = SmtpSettingsService(session)
    row = await svc.upsert(
        enabled=body.enabled, host=body.host, port=body.port, security=body.security,
        username=body.username, from_email=str(body.from_email), from_name=body.from_name,
        password=body.password, clear_password=body.clear_password,
    )
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="smtp.update",
        target_type="smtp_settings", target_id="1", ip=None,
        details={"host": body.host, "enabled": body.enabled},
    )
    out = _out(row)
    await session.commit()
    return out


@router.post("/test", response_model=SmtpTestOut, dependencies=[Depends(enforce_csrf)])
async def test_smtp(
    body: SmtpTestIn,
    user: User = Depends(require_org(Action.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> SmtpTestOut:
    if body.security not in SECURITIES:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="invalid security")
    svc = SmtpSettingsService(session)
    # Password: use the submitted one, else fall back to the stored (encrypted) password.
    password = body.password
    if password is None:
        stored = await svc.get()
        password = svc.to_send_config(stored).password if (stored and stored.password_enc) else None
    cfg = SmtpSendConfig(
        host=body.host, port=body.port, security=body.security, username=body.username,
        password=password, from_email=str(body.from_email), from_name=body.from_name,
    )
    await AuditService(session).record(
        actor_user_id=user.id, tenant_id=None, action="smtp.test",
        target_type="smtp_settings", target_id="1", ip=None, details={"to": str(body.to)},
    )
    await session.commit()
    try:
        await send_report_email(
            cfg, subject="OPNGMS SMTP test", recipients=[str(body.to)],
            body_text="This is a test email from OPNGMS. SMTP delivery is configured correctly.",
            attachment=("opngms-test.txt", b"OPNGMS SMTP test", "text/plain"),
        )
    except EmailSendError as exc:
        return SmtpTestOut(ok=False, detail=str(exc))
    return SmtpTestOut(ok=True, detail="sent")
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py`, add the import (with the other `app.api.*` imports) and `include_router`:

```python
from app.api.smtp import router as smtp_router
...
app.include_router(smtp_router)
```

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_smtp_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/smtp.py backend/app/api/smtp.py backend/app/main.py backend/tests/test_smtp_api.py
git commit -m "feat(reports): superadmin SMTP config + test API"
```

---

## Task A10: ReportScheduleRepository

**Files:**
- Create: `backend/app/repositories/report_schedule.py`
- Test: `backend/tests/test_report_schedule_repo.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_schedule_repo.py
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from app.repositories.report_schedule import ReportScheduleRepository


async def _tenant_device(s):
    tid, did = uuid.uuid4(), uuid.uuid4()
    await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
    await set_tenant_context(s, tid)
    await s.execute(text(
        "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
        "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
    return tid, did


async def test_upsert_tenant_then_device_and_list(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, did = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        now = datetime(2026, 6, 10, 9, tzinfo=UTC)
        t = await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
                              recipients=["a@x.io"], created_by=None, now=now)
        assert t.next_run_at == datetime(2026, 6, 15, 4, tzinfo=UTC)
        d = await repo.upsert(device_id=did, enabled=True, frequency="monthly", weekday=None, hour=5,
                              recipients=["b@x.io"], created_by=None, now=now)
        assert d.next_run_at == datetime(2026, 7, 1, 5, tzinfo=UTC)
        rows = await repo.list()
        assert {r.device_id for r in rows} == {None, did}


async def test_upsert_is_idempotent_per_scope(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, _ = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        now = datetime(2026, 6, 10, 9, tzinfo=UTC)
        await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
                          recipients=["a@x.io"], created_by=None, now=now)
        await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=2, hour=6,
                          recipients=["c@x.io"], created_by=None, now=now)
        rows = await repo.list()
        assert len(rows) == 1  # same scope -> updated, not duplicated
        assert rows[0].weekday == 2 and rows[0].recipients == ["c@x.io"]


async def test_on_demand_has_null_next_run(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, _ = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        r = await repo.upsert(device_id=None, enabled=True, frequency="on_demand", weekday=None,
                              hour=4, recipients=["a@x.io"], created_by=None,
                              now=datetime(2026, 6, 10, 9, tzinfo=UTC))
        assert r.next_run_at is None


async def test_delete(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as s:
        tid, _ = await _tenant_device(s)
        repo = ReportScheduleRepository(s, tid)
        r = await repo.upsert(device_id=None, enabled=True, frequency="weekly", weekday=0, hour=4,
                              recipients=["a@x.io"], created_by=None, now=datetime(2026, 6, 10, 9, tzinfo=UTC))
        assert await repo.delete(r.id) is True
        assert await repo.list() == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedule_repo.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
# backend/app/repositories/report_schedule.py
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report_schedule import ReportSchedule
from app.services.report_schedule import next_run_at


class ReportScheduleRepository:
    def __init__(self, session: AsyncSession, tenant_id: uuid.UUID) -> None:
        self.session = session
        self.tenant_id = tenant_id

    async def list(self) -> list[ReportSchedule]:
        return list((await self.session.execute(
            select(ReportSchedule).where(ReportSchedule.tenant_id == self.tenant_id)
            .order_by(ReportSchedule.device_id.nullsfirst())
        )).scalars().all())

    async def get(self, schedule_id: uuid.UUID) -> ReportSchedule | None:
        return (await self.session.execute(
            select(ReportSchedule).where(
                ReportSchedule.tenant_id == self.tenant_id, ReportSchedule.id == schedule_id
            )
        )).scalar_one_or_none()

    async def _get_by_scope(self, device_id: uuid.UUID | None) -> ReportSchedule | None:
        stmt = select(ReportSchedule).where(ReportSchedule.tenant_id == self.tenant_id)
        stmt = stmt.where(ReportSchedule.device_id.is_(None) if device_id is None
                          else ReportSchedule.device_id == device_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def upsert(self, *, device_id: uuid.UUID | None, enabled: bool, frequency: str,
                     weekday: int | None, hour: int, recipients: list[str],
                     created_by: uuid.UUID | None, now: datetime) -> ReportSchedule:
        row = await self._get_by_scope(device_id)
        if row is None:
            row = ReportSchedule(tenant_id=self.tenant_id, device_id=device_id, created_by=created_by)
            self.session.add(row)
        row.enabled, row.frequency, row.weekday, row.hour = enabled, frequency, weekday, hour
        row.recipients = recipients
        row.next_run_at = next_run_at(frequency, weekday, hour, after=now) if enabled else None
        await self.session.flush()
        return row

    async def delete(self, schedule_id: uuid.UUID) -> bool:
        row = await self.get(schedule_id)
        if row is None:
            return False
        await self.session.delete(row)
        await self.session.flush()
        return True
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedule_repo.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/repositories/report_schedule.py backend/tests/test_report_schedule_repo.py
git commit -m "feat(reports): ReportScheduleRepository (CRUD + next_run_at)"
```

---

## Task A11: Report-schedule schemas + tenant API

**Files:**
- Create: `backend/app/schemas/report_schedule.py`, `backend/app/api/report_schedules.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_report_schedules_api.py`

- [ ] **Step 1: Write the failing test** (mirror tenant-scoped RBAC tests; use `make_membership`)

```python
# backend/tests/test_report_schedules_api.py
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.db import set_tenant_context
from tests.conftest import csrf_headers
from tests.factories import make_membership, make_user


async def _seed(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        admin = await make_user(s, email="admin@x.io", password="pw12345")
        ro = await make_user(s, email="ro@x.io", password="pw12345")
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await make_membership(s, user_id=admin.id, tenant_id=tid, role="tenant_admin")
        await make_membership(s, user_id=ro.id, tenant_id=tid, role="read_only")
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        await s.commit()
    return tid, did


async def _login(api_client, email):
    r = await api_client.post("/api/login", json={"email": email, "password": "pw12345"})
    assert r.status_code == 200, r.text


async def test_tenant_admin_upserts_and_lists(api_client, db_engine):
    tid, did = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    p = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": None, "enabled": True, "frequency": "weekly", "weekday": 0, "hour": 4,
        "recipients": ["A@x.io", "a@x.io"],
    })
    assert p.status_code == 200, p.text
    assert p.json()["recipients"] == ["a@x.io"]  # normalized+deduped
    assert p.json()["next_run_at"] is not None
    g = await api_client.get(f"/api/tenants/{tid}/report-schedules")
    assert len(g.json()) == 1


async def test_weekly_requires_weekday(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": None, "enabled": True, "frequency": "weekly", "weekday": None, "hour": 4,
        "recipients": ["a@x.io"],
    })
    assert r.status_code == 400


async def test_device_must_belong_to_tenant(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "admin@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": str(uuid.uuid4()), "enabled": True, "frequency": "monthly", "weekday": None,
        "hour": 4, "recipients": ["a@x.io"],
    })
    assert r.status_code == 404


async def test_read_only_denied(api_client, db_engine):
    tid, _ = await _seed(db_engine)
    await _login(api_client, "ro@x.io")
    r = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
        "device_id": None, "enabled": True, "frequency": "weekly", "weekday": 0, "hour": 4,
        "recipients": ["a@x.io"],
    })
    assert r.status_code == 403


async def test_send_now_enqueues(api_client, db_engine):
    from app.core.queue import get_enqueuer
    from app.main import app

    calls = []

    async def fake_enqueue(name, *args, **kwargs):
        calls.append((name, args, kwargs))

    app.dependency_overrides[get_enqueuer] = lambda: fake_enqueue
    try:
        tid, _ = await _seed(db_engine)
        await _login(api_client, "admin@x.io")
        p = await api_client.put(f"/api/tenants/{tid}/report-schedules", headers=csrf_headers(api_client), json={
            "device_id": None, "enabled": True, "frequency": "weekly", "weekday": 0, "hour": 4,
            "recipients": ["a@x.io"]})
        sid = p.json()["id"]
        r = await api_client.post(f"/api/tenants/{tid}/report-schedules/{sid}/send-now",
                                  headers=csrf_headers(api_client))
        assert r.status_code == 202, r.text
        assert calls and calls[0][0] == "deliver_scheduled_report"
        assert calls[0][1][0] == sid and calls[0][1][1] is True  # (schedule_id, manual=True)
    finally:
        app.dependency_overrides.pop(get_enqueuer, None)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedules_api.py -v`
Expected: FAIL (router missing).

- [ ] **Step 3: Implement the schemas**

```python
# backend/app/schemas/report_schedule.py
import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class ReportScheduleIn(BaseModel):
    device_id: uuid.UUID | None = None
    enabled: bool = True
    frequency: str  # weekly | monthly | on_demand
    weekday: int | None = Field(default=None, ge=0, le=6)
    hour: int = Field(default=4, ge=0, le=23)
    recipients: list[str] = Field(default_factory=list, max_length=200)


class ReportScheduleOut(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID | None
    enabled: bool
    frequency: str
    weekday: int | None
    hour: int
    recipients: list[str]
    next_run_at: datetime | None
    last_run_at: datetime | None
```

- [ ] **Step 4: Implement the API**

```python
# backend/app/api/report_schedules.py
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.deps import TenantContext, enforce_csrf, require_tenant
from app.core.queue import get_enqueuer
from app.core.rbac import Action
from app.models.device import Device
from app.repositories.report_schedule import ReportScheduleRepository
from app.schemas.report_schedule import ReportScheduleIn, ReportScheduleOut
from app.services.audit import AuditService
from app.services.report_schedule import FREQUENCIES, WEEKLY, normalize_recipients

router = APIRouter(prefix="/api/tenants/{tenant_id}/report-schedules", tags=["report-schedules"])


def _out(row) -> ReportScheduleOut:
    return ReportScheduleOut(
        id=row.id, device_id=row.device_id, enabled=row.enabled, frequency=row.frequency,
        weekday=row.weekday, hour=row.hour, recipients=list(row.recipients or []),
        next_run_at=row.next_run_at, last_run_at=row.last_run_at,
    )


@router.get("", response_model=list[ReportScheduleOut])
async def list_schedules(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.DEVICE_VIEW)),
    session: AsyncSession = Depends(get_session),
) -> list[ReportScheduleOut]:
    return [_out(r) for r in await ReportScheduleRepository(session, tenant_id).list()]


@router.put("", response_model=ReportScheduleOut, dependencies=[Depends(enforce_csrf)])
async def upsert_schedule(
    tenant_id: uuid.UUID,
    body: ReportScheduleIn,
    request: Request,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> ReportScheduleOut:
    if body.frequency not in FREQUENCIES:
        raise HTTPException(status_code=400, detail="invalid frequency")
    if body.frequency == WEEKLY and body.weekday is None:
        raise HTTPException(status_code=400, detail="weekly schedule requires a weekday")
    if body.frequency != WEEKLY and body.weekday is not None:
        raise HTTPException(status_code=400, detail="weekday only valid for weekly")
    if body.device_id is not None:
        device = await session.get(Device, body.device_id)
        if device is None or device.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="Device not found")
    try:
        recipients = normalize_recipients(body.recipients)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    row = await ReportScheduleRepository(session, tenant_id).upsert(
        device_id=body.device_id, enabled=body.enabled, frequency=body.frequency,
        weekday=body.weekday, hour=body.hour, recipients=recipients, created_by=ctx.user.id,
        now=datetime.now(UTC),
    )
    await AuditService(session).record(
        actor_user_id=ctx.user.id, tenant_id=tenant_id, action="report.schedule.upsert",
        target_type="report_schedule", target_id=str(row.id),
        ip=request.client.host if request.client else None,
        details={"device_id": str(body.device_id) if body.device_id else None,
                 "frequency": body.frequency, "enabled": body.enabled},
    )
    out = _out(row)
    await session.commit()
    return out


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(enforce_csrf)])
async def delete_schedule(
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not await ReportScheduleRepository(session, tenant_id).delete(schedule_id):
        raise HTTPException(status_code=404, detail="Schedule not found")
    await session.commit()


@router.post("/{schedule_id}/send-now", status_code=status.HTTP_202_ACCEPTED,
             dependencies=[Depends(enforce_csrf)])
async def send_now(
    tenant_id: uuid.UUID,
    schedule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_tenant(Action.REPORT_CONFIG)),
    session: AsyncSession = Depends(get_session),
    enqueue=Depends(get_enqueuer),
) -> Response:
    row = await ReportScheduleRepository(session, tenant_id).get(schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Schedule not found")
    await enqueue("deliver_scheduled_report", str(schedule_id), True)  # manual=True
    return Response(status_code=status.HTTP_202_ACCEPTED)
```

- [ ] **Step 5: Mount the router**

In `backend/app/main.py` add:

```python
from app.api.report_schedules import router as report_schedules_router
...
app.include_router(report_schedules_router)
```

- [ ] **Step 6: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_schedules_api.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas/report_schedule.py backend/app/api/report_schedules.py backend/app/main.py backend/tests/test_report_schedules_api.py
git commit -m "feat(reports): tenant report-schedule API (CRUD + send-now)"
```

---

## Task A12: Surface from_email in the report-settings API

**Files:**
- Modify: `backend/app/schemas/report_settings.py`, `backend/app/api/reports.py`
- Test: `backend/tests/test_report_settings_api.py` (extend)

- [ ] **Step 1: Write the failing assertion**

Append to `backend/tests/test_report_settings_api.py` (mirror its existing login/seed helpers — read the file first):

```python
async def test_from_email_roundtrips(api_client, db_engine):
    tid = await _seed_tenant_admin(db_engine)  # reuse this file's existing seed helper
    await _login(api_client, "admin@x.io")
    p = await api_client.put(f"/api/tenants/{tid}/reports/settings", headers=csrf_headers(api_client),
                             json={"title": "T", "owner": "o", "timezone": "UTC", "language": "en",
                                   "from_email": "brand@x.io"})
    assert p.status_code == 200, p.text
    assert p.json()["from_email"] == "brand@x.io"
    g = await api_client.get(f"/api/tenants/{tid}/reports/settings")
    assert g.json()["from_email"] == "brand@x.io"
```

> If this file's seed/login helpers have different names, adapt the call — the assertion is what matters.

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_report_settings_api.py::test_from_email_roundtrips -v`
Expected: FAIL (`from_email` not accepted/returned).

- [ ] **Step 3: Implement**

`backend/app/schemas/report_settings.py` — add `from_email` to both models:

```python
class ReportSettingsIn(BaseModel):
    title: str
    owner: str = ""
    timezone: str = "UTC"
    language: str = "en"
    from_email: str = ""


class ReportSettingsOut(BaseModel):
    title: str
    owner: str
    timezone: str
    has_logo: bool
    logo_mime: str | None
    language: str
    from_email: str
```

`backend/app/api/reports.py` — in `_settings_to_out` add `from_email=settings.from_email`, and in `update_report_settings` pass `from_email=body.from_email` to `repo.upsert(...)` and include it in the audit `details`. Validate it: if non-empty, it must parse as an email — add at the top of `update_report_settings`:

```python
    if body.from_email:
        from email_validator import EmailNotValidError, validate_email
        try:
            validate_email(body.from_email, check_deliverability=False)
        except EmailNotValidError as exc:
            raise HTTPException(status_code=400, detail="invalid from_email") from exc
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_settings_api.py -v`
Expected: PASS (all, including the existing ones).

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/report_settings.py backend/app/api/reports.py backend/tests/test_report_settings_api.py
git commit -m "feat(reports): per-tenant from_email sender override in settings API"
```

---

## Task A13: Worker — replace weekly cron with schedule-driven delivery (tenant scope)

This task wires `enqueue_due_reports` + `deliver_scheduled_report` + `send_report_email_job` and
removes the weekly cron. The per-device build is added in Phase B (here `device_id` is threaded but
fleet-only is exercised).

**Files:**
- Modify: `backend/app/worker.py`
- Test: `backend/tests/test_worker_reports.py` (rewrite the cron tests), new `backend/tests/test_worker_delivery.py`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_worker_delivery.py
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.worker as worker
from app.core.db import set_tenant_context
from app.models.generated_report import GeneratedReport
from app.models.report_schedule import ReportSchedule
from app.repositories.report_schedule import ReportScheduleRepository
from app.services.smtp_settings import SmtpSettingsService


class FakeRedis:
    def __init__(self):
        self.calls = []

    async def enqueue_job(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))


async def _seed_schedule(factory, *, enabled=True, next_run_at, frequency="weekly", weekday=0):
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'Acme','acme','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        s.add(ReportSchedule(tenant_id=tid, device_id=None, enabled=enabled, frequency=frequency,
                             weekday=weekday, hour=4, recipients=["a@x.io"], next_run_at=next_run_at))
        await s.commit()
    return tid, did


async def test_enqueue_due_reports_picks_due_only(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))  # due (past)
    await _seed_schedule(factory, next_run_at=datetime(2999, 1, 1, tzinfo=UTC))  # not due
    redis = FakeRedis()
    n = await worker.enqueue_due_reports({"session_factory": factory, "redis": redis})
    assert n == 1
    assert redis.calls[0][0] == "deliver_scheduled_report"


async def test_deliver_builds_stores_advances_and_enqueues_send(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, _ = await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    async with factory() as s:
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()
    redis = FakeRedis()
    res = await worker.deliver_scheduled_report({"session_factory": factory, "redis": redis}, str(sid))
    assert res == "generated"
    async with factory() as s:
        rep = (await s.execute(select(GeneratedReport))).scalar_one()
        assert rep.kind == "scheduled" and rep.pdf[:5] == b"%PDF-"
        sched = await s.get(ReportSchedule, sid)
        assert sched.last_run_at is not None
        assert sched.next_run_at > datetime(2020, 1, 1, tzinfo=UTC)  # advanced
    assert redis.calls[0][0] == "send_report_email_job"
    assert redis.calls[0][1][0] == str(rep.id)


async def test_send_job_delivers_on_success(db_engine, monkeypatch):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, _ = await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    # configure SMTP enabled
    async with factory() as s:
        await SmtpSettingsService(s).upsert(enabled=True, host="h", port=587, security="starttls",
            username="u", from_email="noc@x.io", from_name="N", password="p", clear_password=False)
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()
        await s.commit()
    redis = FakeRedis()
    await worker.deliver_scheduled_report({"session_factory": factory, "redis": redis}, str(sid))
    report_id = redis.calls[0][1][0]

    sent = {}
    async def fake_send(cfg, **kw):
        sent["recipients"] = kw["recipients"]
        sent["from"] = cfg.from_email
    monkeypatch.setattr(worker, "send_report_email", fake_send)
    res = await worker.send_report_email_job({"session_factory": factory, "redis": redis}, report_id, str(sid), 1)
    assert res == "delivered"
    assert sent["recipients"] == ["a@x.io"]


async def test_send_job_retries_then_gives_up(db_engine, monkeypatch):
    from app.services.email.smtp import EmailSendError
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    await _seed_schedule(factory, next_run_at=datetime(2020, 1, 1, tzinfo=UTC))
    async with factory() as s:
        await SmtpSettingsService(s).upsert(enabled=True, host="h", port=587, security="starttls",
            username="u", from_email="noc@x.io", from_name="N", password="p", clear_password=False)
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()
        await s.commit()
    redis = FakeRedis()
    await worker.deliver_scheduled_report({"session_factory": factory, "redis": redis}, str(sid))
    report_id = redis.calls[0][1][0]
    redis.calls.clear()

    async def boom(cfg, **kw):
        raise EmailSendError("nope")
    monkeypatch.setattr(worker, "send_report_email", boom)
    # attempt 1 -> re-enqueues attempt 2 with defer
    r1 = await worker.send_report_email_job({"session_factory": factory, "redis": redis}, report_id, str(sid), 1)
    assert r1 == "retry"
    assert redis.calls[0][0] == "send_report_email_job"
    assert redis.calls[0][1][2] == 2  # attempt incremented
    assert redis.calls[0][2].get("_defer_by") == worker.RETRY_INTERVAL
    # final attempt -> gives up
    r_last = await worker.send_report_email_job({"session_factory": factory, "redis": redis}, report_id, str(sid), worker.MAX_SEND_ATTEMPTS)
    assert r_last == "failed"
```

Then **replace** the obsolete weekly-cron tests in `tests/test_worker_reports.py`. Delete
`test_enqueue_scheduled_reports_enumerates_active_tenants` and
`test_enqueue_scheduled_reports_no_active_tenants` and the `_prior_week` import (they test removed
code). Keep `test_generate_tenant_report_*`? Those test `generate_tenant_report`, which we remove —
delete them too. (The delivery path is fully covered by `test_worker_delivery.py`.)

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && .venv/bin/pytest tests/test_worker_delivery.py -v`
Expected: FAIL (the new worker functions don't exist).

- [ ] **Step 3: Implement the worker changes**

In `backend/app/worker.py`:

(a) Add imports near the top:

```python
from app.services.email.smtp import EmailSendError, send_report_email
from app.services.report_schedule import ON_DEMAND, report_window, next_run_at as _next_run_at
```

(b) Add module constants (near `_settings`):

```python
MAX_SEND_ATTEMPTS = 12          # 1 send + retries every RETRY_INTERVAL, ~2h total
RETRY_INTERVAL = 600            # seconds between send retries
```

(c) **Remove** `_prior_week`, `enqueue_scheduled_reports`, and `generate_tenant_report`. Add:

```python
async def enqueue_due_reports(ctx: dict) -> int:
    """Cron (hourly): enqueue a delivery job for each enabled schedule whose next_run_at is due."""
    from app.models.report_schedule import ReportSchedule

    factory = ctx["session_factory"]
    redis = ctx["redis"]
    now = datetime.now(UTC)
    async with factory() as session:
        ids = (await session.execute(
            select(ReportSchedule.id).where(
                ReportSchedule.enabled.is_(True),
                ReportSchedule.next_run_at.isnot(None),
                ReportSchedule.next_run_at <= now,
            )
        )).scalars().all()
    for sid in ids:
        await redis.enqueue_job("deliver_scheduled_report", str(sid))
    return len(ids)


async def deliver_scheduled_report(ctx: dict, schedule_id: str, manual: bool = False) -> str:
    """Job: build + store a report for a schedule, advance its cadence, enqueue the send.

    Runs as owner (RLS bypassed); the repositories scope every query by explicit tenant_id.
    """
    from app.models.report_schedule import ReportSchedule
    from app.models.tenant import Tenant
    from app.repositories.generated_report import GeneratedReportRepository
    from app.repositories.report_settings import ReportSettingsRepository
    from app.services.audit import AuditService
    from app.services.reporting.service import ReportService

    factory = ctx["session_factory"]
    redis = ctx["redis"]
    now = datetime.now(UTC)
    async with factory() as session:
        sched = await session.get(ReportSchedule, uuid.UUID(schedule_id))
        if sched is None:
            return "missing"
        if not manual and (not sched.enabled or sched.next_run_at is None or sched.next_run_at > now):
            return "skip"
        tenant = await session.get(Tenant, sched.tenant_id)
        if tenant is None:
            return "missing-tenant"
        if sched.device_id is not None:
            from app.models.device import Device
            if await session.get(Device, sched.device_id) is None:
                sched.enabled = False
                await AuditService(session).record(
                    actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.device_missing",
                    target_type="report_schedule", target_id=str(sched.id), ip=None, details={},
                )
                await session.commit()
                return "device-missing"
        frm, to = report_window(sched.frequency, run_at=now)
        settings = await ReportSettingsRepository(session, sched.tenant_id).get_or_default()
        pdf = await ReportService(session, sched.tenant_id).build_report(
            tenant_name=tenant.name, frm=frm, to=to, locale=settings.language,
            device_id=sched.device_id,
        )
        report = await GeneratedReportRepository(session, sched.tenant_id).create(
            kind="scheduled", period_from=frm, period_to=to, created_by=None, pdf=pdf,
            device_id=sched.device_id,
        )
        sched.last_run_at = now
        if not manual and sched.frequency != ON_DEMAND:
            sched.next_run_at = _next_run_at(sched.frequency, sched.weekday, sched.hour, after=now)
        await session.commit()
        await redis.enqueue_job("send_report_email_job", str(report.id), str(sched.id), 1)
        return "generated"


async def send_report_email_job(ctx: dict, report_id: str, schedule_id: str, attempt: int) -> str:
    """Job: email an already-stored report PDF to a schedule's recipients, with retry."""
    from app.models.generated_report import GeneratedReport
    from app.models.report_schedule import ReportSchedule
    from app.models.tenant import Tenant
    from app.repositories.report_settings import ReportSettingsRepository
    from app.services.audit import AuditService
    from app.services.smtp_settings import SmtpSettingsService

    factory = ctx["session_factory"]
    redis = ctx["redis"]

    async def _retry_or_give_up(session, sched, reason: str) -> str:
        if attempt < MAX_SEND_ATTEMPTS:
            await redis.enqueue_job("send_report_email_job", report_id, schedule_id, attempt + 1,
                                    _defer_by=RETRY_INTERVAL)
            return "retry"
        await AuditService(session).record(
            actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.failed",
            target_type="report_schedule", target_id=str(sched.id), ip=None,
            details={"error": reason, "attempts": attempt},
        )
        await session.commit()
        return "failed"

    async with factory() as session:
        sched = await session.get(ReportSchedule, uuid.UUID(schedule_id))
        report = await session.get(GeneratedReport, uuid.UUID(report_id))
        if sched is None or report is None:
            return "missing"
        recipients = list(sched.recipients or [])
        if not recipients:
            await AuditService(session).record(
                actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.no_recipients",
                target_type="report_schedule", target_id=str(sched.id), ip=None, details={},
            )
            await session.commit()
            return "no-recipients"
        svc = SmtpSettingsService(session)
        smtp = await svc.get()
        if smtp is None or not smtp.enabled:
            return await _retry_or_give_up(session, sched, "smtp not configured")
        cfg = svc.to_send_config(smtp)
        settings = await ReportSettingsRepository(session, sched.tenant_id).get_or_default()
        if settings.from_email:
            cfg.from_email = settings.from_email
        tenant = await session.get(Tenant, sched.tenant_id)
        subject = (f"{settings.title} — {tenant.name} — "
                   f"{report.period_from:%Y-%m-%d}..{report.period_to:%Y-%m-%d}")
        try:
            await send_report_email(
                cfg, subject=subject, recipients=recipients,
                body_text="Your scheduled OPNGMS report is attached.",
                attachment=("opngms-report.pdf", report.pdf, "application/pdf"),
            )
        except EmailSendError as exc:
            return await _retry_or_give_up(session, sched, str(exc))
        await AuditService(session).record(
            actor_user_id=None, tenant_id=sched.tenant_id, action="report.schedule.delivered",
            target_type="report_schedule", target_id=str(sched.id), ip=None,
            details={"recipients": len(recipients), "report_id": str(report.id)},
        )
        await session.commit()
        return "delivered"
```

(d) Update `WorkerSettings`:

```python
class WorkerSettings:
    functions = [poll_device, ingest_device_events, backup_device_config, apply_config_change, run_firmware_action, deliver_scheduled_report, send_report_email_job]
    cron_jobs = [
        cron(enqueue_device_polls, second={0}),
        cron(enqueue_event_ingests, minute=set(range(0, 60, _ingest_step))),
        cron(enqueue_config_backups, hour={_settings.config_backup_hour}, minute={0}),
        cron(enqueue_due_reports, minute={0}),  # hourly: fire due report schedules
        cron(cleanup_expired_sessions, minute={_settings.session_cleanup_minute}),
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
```

> Note: `cfg.from_email = settings.from_email` mutates the dataclass — `SmtpSendConfig` is a plain
> (mutable) dataclass, so this is fine. The `report_weekday`/`report_hour` settings in `config.py`
> are now unused but harmless; leave them (removing settings is out of scope).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_worker_delivery.py tests/test_worker_reports.py -v`
Expected: PASS (`test_worker_delivery` green; `test_worker_reports` now only has whatever you kept — if you removed all its tests, delete the file instead and skip it here).

- [ ] **Step 5: Full backend regression**

Run: `cd backend && .venv/bin/pytest -q`
Expected: PASS (no references to the removed `generate_tenant_report`/`enqueue_scheduled_reports`/`_prior_week` remain — grep to be sure: `grep -rn "generate_tenant_report\|enqueue_scheduled_reports\|_prior_week" app tests`).

- [ ] **Step 6: Commit**

```bash
git add backend/app/worker.py backend/tests/test_worker_delivery.py backend/tests/test_worker_reports.py
git commit -m "feat(reports): schedule-driven worker delivery with send-retry"
```

---

## Task A14: Regenerate the OpenAPI client types

The frontend's typed client (`api.GET("/api/admin/smtp")`, etc.) only compiles once `schema.d.ts`
includes the new paths.

**Files:**
- Modify: `frontend/src/api/schema.d.ts` (generated), `frontend/openapi.json` (generated)

- [ ] **Step 1: Regenerate**

Run: `cd frontend && npm run gen:api`
Expected: rewrites `openapi.json` + `src/api/schema.d.ts` with the new `/api/admin/smtp`,
`/api/admin/smtp/test`, `/api/tenants/{tenant_id}/report-schedules*` paths and the `from_email` field.

- [ ] **Step 2: Sanity-check the new paths exist**

Run: `cd frontend && grep -c "report-schedules" src/api/schema.d.ts && grep -c "admin/smtp" src/api/schema.d.ts`
Expected: both > 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/schema.d.ts frontend/openapi.json
git commit -m "chore(api): regenerate OpenAPI client for report delivery endpoints"
```

---

## Task A15: Frontend — SMTP settings page (superadmin)

**Files:**
- Create: `frontend/src/admin/smtpHooks.ts`, `frontend/src/pages/SmtpSettingsPage.tsx`
- Modify: `frontend/src/components/AppShell.tsx`, `frontend/src/i18n/en.ts`
- Test: `frontend/src/pages/__tests__/smtpSettings.test.tsx`

> Mirror `src/security/mfaHooks.ts` (query+mutation shape) and `src/security/MfaPanel.tsx`
> (superadmin gate via `useAuth().me?.is_superadmin`). Reuse the page wrapper from
> `src/pages/ReportSettingsPage.tsx` (`<Stack maw=…><Title/><Card withBorder…>`).

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/__tests__/smtpSettings.test.tsx
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SmtpSettingsPage } from "../SmtpSettingsPage";
import { server } from "../../test/server";
import { renderWithProviders, withAuth } from "../../test/utils";  // confirm helper names

const SMTP = "/api/admin/smtp";

describe("SmtpSettingsPage", () => {
  it("loads, saves config (PUT) and runs a test send", async () => {
    let putBody: unknown = null;
    server.use(
      http.get(SMTP, () => HttpResponse.json({
        enabled: false, host: "", port: 587, security: "starttls", username: null,
        from_email: "", from_name: "", has_password: false })),
      http.put(SMTP, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ ...(putBody as object), has_password: true });
      }),
      http.post(`${SMTP}/test`, () => HttpResponse.json({ ok: true, detail: "sent" })),
    );

    renderWithProviders(withAuth(<SmtpSettingsPage />, true));  // true = superadmin
    await userEvent.type(await screen.findByTestId("smtp-host"), "smtp.x.io");
    await userEvent.type(screen.getByTestId("smtp-from-email"), "noc@x.io");
    await userEvent.click(screen.getByTestId("smtp-save"));
    await waitFor(() => expect((putBody as { host: string }).host).toBe("smtp.x.io"));
  });

  it("blocks non-superadmin", () => {
    renderWithProviders(withAuth(<SmtpSettingsPage />, false));
    expect(screen.getByTestId("smtp-forbidden")).toBeInTheDocument();
  });
});
```

> If `withAuth`/`renderWithProviders` differ, open `src/security/__tests__/mfaPanel.test.tsx` and copy
> its exact import + helper usage.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- smtpSettings`
Expected: FAIL (page module missing).

- [ ] **Step 3: Implement the hooks**

```ts
// frontend/src/admin/smtpHooks.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type SmtpOut = components["schemas"]["SmtpSettingsOut"];
export type SmtpIn = components["schemas"]["SmtpSettingsIn"];
export type SmtpTestIn = components["schemas"]["SmtpTestIn"];

const smtpKey = () => ["smtp-settings"] as const;

export function useSmtpSettings() {
  return useQuery({
    queryKey: smtpKey(),
    queryFn: async (): Promise<SmtpOut> => {
      const { data, error } = await api.GET("/api/admin/smtp");
      if (error || !data) throw new Error("Failed to load SMTP settings");
      return data;
    },
  });
}

export function useUpdateSmtpSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: SmtpIn): Promise<SmtpOut> => {
      const { data, error } = await api.PUT("/api/admin/smtp", { body });
      if (error || !data) throw new Error("Failed to save SMTP settings");
      return data;
    },
    onSuccess: (data) => qc.setQueryData(smtpKey(), data),
  });
}

export function useTestSmtp() {
  return useMutation({
    mutationFn: async (body: SmtpTestIn) => {
      const { data, error } = await api.POST("/api/admin/smtp/test", { body });
      if (error || !data) throw new Error("Test send failed");
      return data;  // { ok, detail }
    },
  });
}
```

- [ ] **Step 4: Implement the page**

```tsx
// frontend/src/pages/SmtpSettingsPage.tsx
import { useEffect, useRef, useState } from "react";
import {
  Alert, Button, Card, Group, NumberInput, PasswordInput, Select, Stack, Switch, Text,
  TextInput, Title,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { notifications } from "@mantine/notifications";

import { useAuth } from "../auth/useAuth";  // confirm hook path/name
import { useSmtpSettings, useTestSmtp, useUpdateSmtpSettings } from "../admin/smtpHooks";

export function SmtpSettingsPage() {
  const { me } = useAuth();
  const query = useSmtpSettings();
  const update = useUpdateSmtpSettings();
  const test = useTestSmtp();
  const initialized = useRef(false);
  const [testTo, setTestTo] = useState("");

  const form = useForm({
    initialValues: {
      enabled: false, host: "", port: 587, security: "starttls", username: "",
      from_email: "", from_name: "", password: "",
    },
  });

  useEffect(() => {
    if (query.data && !initialized.current) {
      form.setValues({
        enabled: query.data.enabled, host: query.data.host, port: query.data.port,
        security: query.data.security, username: query.data.username ?? "",
        from_email: query.data.from_email, from_name: query.data.from_name, password: "",
      });
      initialized.current = true;
    }
  }, [query.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (!me?.is_superadmin) {
    return <Alert color="red" data-testid="smtp-forbidden">Superadmin only.</Alert>;
  }

  function payload(includePassword: boolean) {
    const v = form.values;
    return {
      enabled: v.enabled, host: v.host, port: v.port, security: v.security,
      username: v.username || null, from_email: v.from_email, from_name: v.from_name,
      ...(includePassword && v.password ? { password: v.password } : {}),
    };
  }

  async function handleSave() {
    try {
      await update.mutateAsync(payload(true) as never);
      form.setFieldValue("password", "");
      notifications.show({ message: "SMTP settings saved" });
    } catch {
      notifications.show({ color: "red", message: "Failed to save SMTP settings" });
    }
  }

  async function handleTest() {
    try {
      const res = await test.mutateAsync({ ...payload(true), to: testTo } as never);
      notifications.show({ color: res.ok ? "green" : "red", message: res.ok ? "Test email sent" : `Test failed: ${res.detail}` });
    } catch {
      notifications.show({ color: "red", message: "Test send failed" });
    }
  }

  return (
    <Stack maw={520}>
      <Title order={3}>SMTP delivery</Title>
      <Text size="sm" c="dimmed">Outbound mail server for scheduled report delivery.</Text>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Switch label="Enable delivery" {...form.getInputProps("enabled", { type: "checkbox" })} data-testid="smtp-enabled" />
          <TextInput label="Host" {...form.getInputProps("host")} data-testid="smtp-host" />
          <Group grow>
            <NumberInput label="Port" {...form.getInputProps("port")} data-testid="smtp-port" />
            <Select label="Security" data={["starttls", "tls", "none"]} {...form.getInputProps("security")} data-testid="smtp-security" />
          </Group>
          <TextInput label="Username" {...form.getInputProps("username")} data-testid="smtp-username" />
          <PasswordInput label={query.data?.has_password ? "Password (leave blank to keep)" : "Password"} {...form.getInputProps("password")} data-testid="smtp-password" />
          <TextInput label="From email" {...form.getInputProps("from_email")} data-testid="smtp-from-email" />
          <TextInput label="From name" {...form.getInputProps("from_name")} data-testid="smtp-from-name" />
          <Group justify="space-between">
            <Button onClick={handleSave} loading={update.isPending} data-testid="smtp-save">Save</Button>
          </Group>
        </Stack>
      </Card>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Title order={5}>Send a test email</Title>
          <Group align="end">
            <TextInput label="Recipient" value={testTo} onChange={(e) => setTestTo(e.currentTarget.value)} data-testid="smtp-test-to" />
            <Button variant="light" onClick={handleTest} loading={test.isPending} disabled={!testTo} data-testid="smtp-test">Send test</Button>
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
```

- [ ] **Step 5: Wire route + nav**

In `frontend/src/components/AppShell.tsx`: lazy-import `SmtpSettingsPage`, add a `<Route path="/admin/smtp" …>`, and a nav item gated by `me?.is_superadmin` (mirror the MFA admin nav gate). Use the exact lazy-import + Routes + nav patterns already in that file.

- [ ] **Step 6: Run to verify pass + build**

Run: `cd frontend && npm test -- smtpSettings && npm run build`
Expected: tests PASS; `tsc -b && vite build` succeeds.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/admin/smtpHooks.ts frontend/src/pages/SmtpSettingsPage.tsx frontend/src/components/AppShell.tsx frontend/src/i18n/en.ts frontend/src/pages/__tests__/smtpSettings.test.tsx
git commit -m "feat(reports): superadmin SMTP settings page"
```

---

## Task A16: Frontend — report settings from_email field

**Files:**
- Modify: `frontend/src/reports/settingsHooks.ts`, `frontend/src/pages/ReportSettingsPage.tsx`, `frontend/src/i18n/en.ts`
- Test: `frontend/src/pages/__tests__/reportsettings.test.tsx` (extend)

- [ ] **Step 1: Add a failing assertion**

In `reportsettings.test.tsx`, extend the existing save test (or add one) to type into a
`field-sender-email` input and assert the PUT body carries `from_email`. Mirror the file's existing
MSW GET/PUT handlers; add `from_email: ""` to the GET payload and assert it round-trips.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- reportsettings`
Expected: FAIL (no `field-sender-email`).

- [ ] **Step 3: Implement**

- `settingsHooks.ts`: the `ReportSettingsOut`/`ReportSettingsIn` types come from `components["schemas"]`, so they already include `from_email` after Task A14 — no type change needed; just ensure the form sends it.
- `ReportSettingsPage.tsx`: add `from_email: ""` to `initialValues`, set it from `settingsQuery.data` in the load effect, and render:

```tsx
<TextInput
  label={t.reports.settings.senderEmail}
  {...form.getInputProps("from_email")}
  data-testid="field-sender-email"
/>
```

- `i18n/en.ts`: add `senderEmail: "Sender email (overrides global)"` under `reports.settings`.

- [ ] **Step 4: Run to verify pass + build**

Run: `cd frontend && npm test -- reportsettings && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ReportSettingsPage.tsx frontend/src/reports/settingsHooks.ts frontend/src/i18n/en.ts frontend/src/pages/__tests__/reportsettings.test.tsx
git commit -m "feat(reports): sender-email field in report settings UI"
```

---

## Task A17: Frontend — report schedule editor (tenant scope)

**Files:**
- Create: `frontend/src/reports/scheduleHooks.ts`, `frontend/src/pages/ReportSchedulePage.tsx`
- Modify: `frontend/src/components/AppShell.tsx`, `frontend/src/i18n/en.ts`
- Test: `frontend/src/pages/__tests__/reportSchedule.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/pages/__tests__/reportSchedule.test.tsx
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReportSchedulePage } from "../ReportSchedulePage";
import { server } from "../../test/server";
import { renderWithProviders, withTenant } from "../../test/utils";  // confirm tenant helper

const BASE = "/api/tenants/:tid/report-schedules";

describe("ReportSchedulePage", () => {
  it("creates the tenant (fleet) schedule", async () => {
    let putBody: unknown = null;
    server.use(
      http.get(BASE, () => HttpResponse.json([])),
      http.put(BASE, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ id: "11111111-1111-1111-1111-111111111111", device_id: null,
          enabled: true, frequency: "weekly", weekday: 0, hour: 4, recipients: ["a@x.io"],
          next_run_at: "2026-06-15T04:00:00Z", last_run_at: null });
      }),
    );
    renderWithProviders(withTenant(<ReportSchedulePage />, "tenant_admin"));
    await userEvent.type(await screen.findByTestId("fleet-recipients"), "a@x.io");
    await userEvent.click(screen.getByTestId("fleet-save"));
    await waitFor(() => expect((putBody as { frequency: string }).frequency).toBe("weekly"));
  });
});
```

> Confirm `withTenant`'s exact name/signature from an existing tenant-scoped page test (e.g. the
> reportsettings test, which renders within a tenant context).

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- reportSchedule`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement the hooks**

```ts
// frontend/src/reports/scheduleHooks.ts
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";  // confirm path
import type { components } from "../api/schema";

export type ScheduleOut = components["schemas"]["ReportScheduleOut"];
export type ScheduleIn = components["schemas"]["ReportScheduleIn"];

const key = (tid: string | undefined) => ["report-schedules", tid] as const;

export function useReportSchedules() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: key(activeId),
    enabled: !!activeId,
    queryFn: async (): Promise<ScheduleOut[]> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/report-schedules",
        { params: { path: { tenant_id: activeId! } } });
      if (error || !data) throw new Error("Failed to load schedules");
      return data;
    },
  });
}

export function useUpsertReportSchedule() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ScheduleIn): Promise<ScheduleOut> => {
      const { data, error } = await api.PUT("/api/tenants/{tenant_id}/report-schedules",
        { params: { path: { tenant_id: activeId! } }, body });
      if (error || !data) throw new Error("Failed to save schedule");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key(activeId) }),
  });
}

export function useDeleteReportSchedule() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.DELETE("/api/tenants/{tenant_id}/report-schedules/{schedule_id}",
        { params: { path: { tenant_id: activeId!, schedule_id: id } } });
      if (error) throw new Error("Failed to delete schedule");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key(activeId) }),
  });
}

export function useSendScheduleNow() {
  const { activeId } = useTenant();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.POST("/api/tenants/{tenant_id}/report-schedules/{schedule_id}/send-now",
        { params: { path: { tenant_id: activeId!, schedule_id: id } } });
      if (error) throw new Error("Failed to send now");
    },
  });
}
```

- [ ] **Step 4: Implement the page (tenant/fleet schedule editor)**

```tsx
// frontend/src/pages/ReportSchedulePage.tsx
import { useEffect, useRef, useState } from "react";
import {
  Alert, Button, Card, Group, NumberInput, Select, Stack, Switch, Text, Textarea, Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { useTenant } from "../tenant/useTenant";
import {
  useReportSchedules, useSendScheduleNow, useUpsertReportSchedule,
} from "../reports/scheduleHooks";

const WEEKDAYS = [
  { value: "0", label: "Monday" }, { value: "1", label: "Tuesday" }, { value: "2", label: "Wednesday" },
  { value: "3", label: "Thursday" }, { value: "4", label: "Friday" }, { value: "5", label: "Saturday" },
  { value: "6", label: "Sunday" },
];

export function ReportSchedulePage() {
  const { activeId, tenants } = useTenant();
  const role = tenants.find((tn) => tn.id === activeId)?.role ?? null;
  const query = useReportSchedules();
  const upsert = useUpsertReportSchedule();
  const sendNow = useSendScheduleNow();
  const loaded = useRef(false);

  const [enabled, setEnabled] = useState(true);
  const [frequency, setFrequency] = useState("weekly");
  const [weekday, setWeekday] = useState<string | null>("0");
  const [hour, setHour] = useState(4);
  const [recipients, setRecipients] = useState("");

  const fleet = query.data?.find((s) => s.device_id === null);

  useEffect(() => {
    if (query.data && !loaded.current) {
      if (fleet) {
        setEnabled(fleet.enabled); setFrequency(fleet.frequency);
        setWeekday(fleet.weekday === null ? null : String(fleet.weekday));
        setHour(fleet.hour); setRecipients((fleet.recipients ?? []).join("\n"));
      }
      loaded.current = true;
    }
  }, [query.data]);  // eslint-disable-line react-hooks/exhaustive-deps

  if (role !== "tenant_admin") {
    return <Alert color="red" data-testid="schedule-forbidden">Tenant admins only.</Alert>;
  }

  async function save() {
    try {
      await upsert.mutateAsync({
        device_id: null, enabled, frequency,
        weekday: frequency === "weekly" ? Number(weekday ?? 0) : null,
        hour, recipients: recipients.split(/[\n,]/).map((r) => r.trim()).filter(Boolean),
      } as never);
      notifications.show({ message: "Schedule saved" });
    } catch {
      notifications.show({ color: "red", message: "Failed to save schedule" });
    }
  }

  async function triggerNow() {
    if (!fleet) return;
    try {
      await sendNow.mutateAsync(fleet.id);
      notifications.show({ message: "Report send queued" });
    } catch {
      notifications.show({ color: "red", message: "Failed to queue send" });
    }
  }

  return (
    <Stack maw={560}>
      <Title order={3}>Report delivery schedule</Title>
      <Text size="sm" c="dimmed">Email the fleet report to recipients on a cadence.</Text>
      <Card withBorder padding="lg" radius="md">
        <Stack>
          <Switch label="Enabled" checked={enabled} onChange={(e) => setEnabled(e.currentTarget.checked)} data-testid="fleet-enabled" />
          <Select label="Frequency" data={[
            { value: "weekly", label: "Weekly" }, { value: "monthly", label: "Monthly (1st)" },
            { value: "on_demand", label: "On demand only" },
          ]} value={frequency} onChange={(v) => setFrequency(v ?? "weekly")} data-testid="fleet-frequency" />
          {frequency === "weekly" && (
            <Select label="Day of week" data={WEEKDAYS} value={weekday} onChange={setWeekday} data-testid="fleet-weekday" />
          )}
          {frequency !== "on_demand" && (
            <NumberInput label="Hour (UTC)" min={0} max={23} value={hour} onChange={(v) => setHour(Number(v))} data-testid="fleet-hour" />
          )}
          <Textarea label="Recipients (one per line)" value={recipients} onChange={(e) => setRecipients(e.currentTarget.value)} data-testid="fleet-recipients" autosize minRows={2} />
          <Group>
            <Button onClick={save} loading={upsert.isPending} data-testid="fleet-save">Save</Button>
            {fleet && <Button variant="light" onClick={triggerNow} loading={sendNow.isPending} data-testid="fleet-send-now">Send now</Button>}
          </Group>
        </Stack>
      </Card>
    </Stack>
  );
}
```

- [ ] **Step 5: Wire route + nav**

In `AppShell.tsx`: lazy-import `ReportSchedulePage`, add a `<Route path="/reports/schedule" …>`, and a
nav item (visible to tenant members; the page itself gates to tenant_admin). Add i18n strings as
needed.

- [ ] **Step 6: Run to verify pass + build**

Run: `cd frontend && npm test -- reportSchedule && npm run build`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/reports/scheduleHooks.ts frontend/src/pages/ReportSchedulePage.tsx frontend/src/components/AppShell.tsx frontend/src/i18n/en.ts frontend/src/pages/__tests__/reportSchedule.test.tsx
git commit -m "feat(reports): tenant report-schedule editor + send-now UI"
```

---

# PHASE B — per-device report scope

## Task B1: Single-device report rendering

**Files:**
- Modify: `backend/app/services/reporting/aggregation.py`, `backend/app/services/reporting/context.py`, `backend/app/services/reporting/service.py`
- Test: `backend/tests/test_report_context.py` (extend) or new `backend/tests/test_report_device_scope.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_report_device_scope.py
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.services.reporting.service import ReportService


async def _seed_two_devices(factory):
    tid, d1, d2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        for did, name in [(d1, "fw-1"), (d2, "fw-2")]:
            await s.execute(text(
                "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
                "VALUES (:i,:t,:n,'https://x',''::bytea,''::bytea,true,'reachable','{}')"),
                {"i": did, "t": tid, "n": name})
        await s.commit()
    return tid, d1, d2


async def test_device_scoped_report_has_one_section(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, d1, _ = await _seed_two_devices(factory)
    frm = datetime(2026, 6, 1, tzinfo=UTC)
    to = datetime(2026, 6, 8, tzinfo=UTC)
    async with factory() as s:
        html = await ReportService(s, tid).build_html(tenant_name="A", frm=frm, to=to, device_id=d1)
    assert "fw-1" in html and "fw-2" not in html


async def test_fleet_report_has_all_sections(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, _, _ = await _seed_two_devices(factory)
    frm = datetime(2026, 6, 1, tzinfo=UTC)
    to = datetime(2026, 6, 8, tzinfo=UTC)
    async with factory() as s:
        html = await ReportService(s, tid).build_html(tenant_name="A", frm=frm, to=to)
    assert "fw-1" in html and "fw-2" in html
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && .venv/bin/pytest tests/test_report_device_scope.py -v`
Expected: FAIL (`build_html` has no `device_id` kwarg).

- [ ] **Step 3: Implement**

`aggregation.py` — add a single-device fetch on `ReportAggregator`:

```python
    async def device(self, device_id: uuid.UUID) -> DeviceRow | None:
        row = (await self.session.execute(
            text("SELECT id, name FROM devices WHERE tenant_id = :tid AND id = :did"),
            {"tid": self.tenant_id, "did": device_id},
        )).one_or_none()
        return DeviceRow(id=row.id, name=row.name) if row else None
```

`context.py` — add `device_id` to `build_context` and use it to pick the device list:

```python
async def build_context(
    aggregator: ReportAggregator,
    *,
    tenant_name: str,
    timezone_name: str,
    owner: str | None,
    frm: datetime,
    to: datetime,
    title: str = "Security & Activity Report",
    logo_data_uri: str | None = None,
    locale: str = "en",
    device_id: uuid.UUID | None = None,
) -> ReportContext:
    ...
    bucket = pick_bucket(to - frm)
    sections: list[DeviceSection] = []
    if device_id is not None:
        one = await aggregator.device(device_id)
        devices = [one] if one is not None else []
    else:
        devices = await aggregator.devices()
    for dev in devices:
        ...
```

(Leave the rest of the loop unchanged.)

`service.py` — thread `device_id` through both methods:

```python
    async def build_html(self, *, tenant_name: str, frm: datetime, to: datetime,
                         locale: str | None = None, device_id: uuid.UUID | None = None) -> str:
        ...
        ctx = await build_context(
            agg, tenant_name=tenant_name, timezone_name=settings.timezone,
            owner=settings.owner or None, frm=frm, to=to, title=settings.title,
            logo_data_uri=ctx_logo, locale=effective, device_id=device_id,
        )
        return render_html(ctx)

    async def build_report(self, *, tenant_name: str, frm: datetime, to: datetime,
                           locale: str | None = None, device_id: uuid.UUID | None = None) -> bytes:
        html = await self.build_html(tenant_name=tenant_name, frm=frm, to=to, locale=locale, device_id=device_id)
        return html_to_pdf(html)
```

Add `import uuid` to `context.py` if not present (it imports `datetime` already; add `import uuid`).

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/bin/pytest tests/test_report_device_scope.py tests/test_report_context.py tests/test_report_engine.py -v`
Expected: PASS (existing report tests still green; the worker already passes `device_id=sched.device_id`, which is `None` for fleet — unchanged behaviour).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/reporting/aggregation.py backend/app/services/reporting/context.py backend/app/services/reporting/service.py backend/tests/test_report_device_scope.py
git commit -m "feat(reports): single-device report scope (build_context device_id)"
```

---

## Task B2: Worker — device-scope delivery end-to-end test

The worker already passes `sched.device_id` (Task A13). Add a test proving a device-scoped schedule
produces a one-device report stored with the right `device_id`.

**Files:**
- Test: `backend/tests/test_worker_delivery.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
async def test_device_scoped_delivery_stores_device_report(db_engine):
    import uuid
    from datetime import UTC, datetime
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    import app.worker as worker
    from app.core.db import set_tenant_context
    from app.models.generated_report import GeneratedReport
    from app.models.report_schedule import ReportSchedule

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    tid, did = uuid.uuid4(), uuid.uuid4()
    async with factory() as s:
        await s.execute(text("INSERT INTO tenants (id,name,slug,status) VALUES (:i,'A','a','active')"), {"i": tid})
        await set_tenant_context(s, tid)
        await s.execute(text(
            "INSERT INTO devices (id,tenant_id,name,base_url,api_key_enc,api_secret_enc,verify_tls,status,tags) "
            "VALUES (:i,:t,'fw-x','https://x',''::bytea,''::bytea,true,'reachable','{}')"), {"i": did, "t": tid})
        s.add(ReportSchedule(tenant_id=tid, device_id=did, enabled=True, frequency="monthly",
                             weekday=None, hour=4, recipients=["a@x.io"],
                             next_run_at=datetime(2020, 1, 1, tzinfo=UTC)))
        await s.commit()
        sid = (await s.execute(select(ReportSchedule.id))).scalar_one()

    class FakeRedis:
        def __init__(self): self.calls = []
        async def enqueue_job(self, name, *a, **k): self.calls.append((name, a, k))

    res = await worker.deliver_scheduled_report({"session_factory": factory, "redis": FakeRedis()}, str(sid))
    assert res == "generated"
    async with factory() as s:
        rep = (await s.execute(select(GeneratedReport))).scalar_one()
        assert rep.device_id == did
```

- [ ] **Step 2: Run to verify it fails / passes**

Run: `cd backend && .venv/bin/pytest tests/test_worker_delivery.py::test_device_scoped_delivery_stores_device_report -v`
Expected: PASS immediately (the worker already threads `device_id`; this test locks the behaviour). If it fails, the regression is in Task A13/B1 — fix there.

- [ ] **Step 3: Commit**

```bash
git add backend/tests/test_worker_delivery.py
git commit -m "test(reports): device-scoped scheduled delivery"
```

---

## Task B3: Frontend — per-device schedule list

Extend `ReportSchedulePage` with a per-device section: list the tenant's devices, each with its own
schedule editor row (frequency/weekday/hour/recipients/send-now), `device_id` set.

**Files:**
- Modify: `frontend/src/pages/ReportSchedulePage.tsx`, `frontend/src/i18n/en.ts`
- Test: `frontend/src/pages/__tests__/reportSchedule.test.tsx` (extend)

- [ ] **Step 1: Add a failing test**

Add a test that mocks `GET /api/tenants/:tid/devices` (confirm the exact devices path/key from
`src/devices/…` hooks) returning two devices, renders the page, and asserts two
`device-schedule-row-{id}` blocks appear; then types recipients into one and saves, asserting the PUT
body carries that `device_id`.

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- reportSchedule`
Expected: FAIL (no per-device rows).

- [ ] **Step 3: Implement**

- Reuse the tenant's device-list hook (find it under `src/devices/` — the same one the devices page
  uses). For each device, render a compact editor bound to that device's existing schedule
  (`query.data?.find((s) => s.device_id === device.id)`), defaulting to disabled/weekly when absent.
- Factor the fleet editor's controls into a small `<ScheduleEditor scope=… initial=… onSave=… onSendNow=…/>`
  component (same file) to avoid duplication between the fleet block and each device row. Each device
  row calls `upsert.mutateAsync({ device_id: device.id, … })` and `sendNow` with that schedule's id
  (only when it already exists).
- Add `data-testid={`device-schedule-row-${device.id}`}` on each row.

- [ ] **Step 4: Run to verify pass + build**

Run: `cd frontend && npm test -- reportSchedule && npm run build`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/ReportSchedulePage.tsx frontend/src/i18n/en.ts frontend/src/pages/__tests__/reportSchedule.test.tsx
git commit -m "feat(reports): per-device report schedule editors"
```

---

## Task B4: Docs — README + .env.example

**Files:**
- Modify: `README.md`, `.env.example`

- [ ] **Step 1: Update `.env.example`**

Add a short note (no new required vars — SMTP is configured in-app by the superadmin, not via env):

```
# --- Report email delivery ---
# SMTP for scheduled report delivery is configured IN-APP by the superadmin
# (Admin → SMTP delivery), NOT here. The password is encrypted at rest with MASTER_KEY.
```

- [ ] **Step 2: Update `README.md`**

In the features/roadmap section, document: superadmin SMTP config + test; per-tenant & per-device
report schedules (weekly/monthly/on-demand) with multiple recipients; per-tenant sender override;
send-now; send-retry (every 10 min, ≤2h). Mark the milestone done in the roadmap table. Keep the
existing structure (see memory: keep README updated per milestone).

- [ ] **Step 3: Commit**

```bash
git add README.md .env.example
git commit -m "docs(reports): document report email delivery & scheduling"
```

---

## Final verification (before finishing the branch)

- [ ] **Backend full suite:** `cd backend && .venv/bin/pytest -q` → all pass.
- [ ] **Frontend build + tests:** `cd frontend && npm run build && npm test` → all pass.
- [ ] **Grep for removed symbols:** `grep -rn "generate_tenant_report\|enqueue_scheduled_reports\|_prior_week" backend/app backend/tests` → no hits.
- [ ] **Security review:** dispatch the `security-reviewer` agent over the diff (SMTP creds encryption + write-only exposure, superadmin gating + CSRF on SMTP/test, tenant RBAC + device-belongs-to-tenant on schedules, RLS on `report_schedule`, recipient validation/caps, header-injection defence, worker owner-scoping). Address BLOCKER/IMPORTANT findings.
- [ ] **Finish:** use `superpowers:finishing-a-development-branch` → push + open PR(s) with green CI, then merge per the protected-main flow.

---

## Self-review notes (author)

- **Spec coverage:** SMTP singleton (A4/A7/A8/A9) ✓; per-tenant sender override (A6/A12/A16) ✓; tenant+device schedules with own recipients (A5/A7/A10/A11/A17/B3) ✓; weekly/monthly/on-demand + send-now (A2/A10/A11/A17) ✓; encrypted password never returned (A8/A9) ✓; send-retry every 10 min ≤2h (A13) ✓; per-device report scope (B1/B2/B3) ✓; language picker already present, sender folded in (A16) ✓; worker replaces weekly cron (A13) ✓; RLS on report_schedule (A5/A7) ✓; OAuth = recorded future TODO (no task — correct).
- **Type consistency:** `next_run_at(frequency, weekday, hour, *, after)` and `report_window(frequency, *, run_at)` used identically in A2/A10/A13; `SmtpSendConfig` fields identical in A3/A8/A9/A13; `GeneratedReportRepository.create(..., device_id=None)` consistent A6/A13; `build_report(..., device_id=None)` consistent B1/A13; worker job arg order `(report_id, schedule_id, attempt)` consistent A13 enqueue + signature.
- **Phasing:** Phase A ships working tenant delivery on its own; Phase B adds device scope. Could be two PRs at finish time; one branch.
