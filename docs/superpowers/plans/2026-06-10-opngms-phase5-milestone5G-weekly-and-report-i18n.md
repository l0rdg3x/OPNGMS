# OPNGMS — Phase 5 / Milestone 5G: Weekly Schedule + Translatable Report Content — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Make the scheduled report **weekly** (prior calendar week), and make every user-facing report string **translatable** via a server-side locale layer (en dict + resolver, fallback to en, ready for more locales — like the frontend i18n).

**Architecture:** `app/worker.py` switches the cron to weekly (`_prior_week`). A new `app/services/reporting/i18n.py` holds the en string dict + a `report_text(locale)` resolver; `build_context`/`ReportService` thread a `locale` (default "en") and attach a `ReportText` to `ReportContext.t`; the template, context, mock provider, and `line_chart` read all strings from `t` (no hardcoded user-facing literals). No data/aggregation changes; still escaped + secret-safe.

**Tech Stack:** Python 3.12+, ARQ cron, Jinja2 (autoescape), WeasyPrint named strings; pytest.

---

## Context for the implementer (read first)

- `app/worker.py` has `_prior_month(now)`, `enqueue_scheduled_reports`, and `cron(enqueue_scheduled_reports, day={1}, hour={4}, minute={0})`. ARQ's `cron(...)` accepts a `weekday=` arg (name like `"mon"` or int 0=Mon..6=Sun — verify against the installed arq and use a name). `datetime.now(timezone.utc)` is fine in the worker.
- `app/services/reporting/context.py` `build_context(aggregator, *, tenant_name, timezone_name, owner, frm, to, title=..., logo_data_uri=None)` builds the per-device blocks: `RankedTable(<title>, (<col0>, <col1>), rows)` with English literals (Top Attempts/Signature/Count, Top Targets/Target, Top Initiators/Initiator, Top Sites/Site/Hits, Top Blocked/Domain/Blocks) and `line_chart(..., y_label="Attempts"/"DNS lookups"/"Data / period"/"Status", x_label="Time", y_format=human_bytes/_updown_fmt)`. It has `human_bytes`, `_updown_fmt`. `ReportContext` has fields `tenant_name,title,timezone,owner,range_from,range_to,sections,logo_data_uri`.
- `app/services/reporting/mock_sections.py` builds `ThreatRankedTable("Top Detected", ("Application","Sessions"), …)`, `("Top Blocked", ("Application","Blocks"))`, `("Top Categories", ("Category","Sessions"/"Requests"))`, `RankedTable("Top Initiators", ("Initiator","Sessions"/"Requests"))`, `RankedTable("Top Sites", ("Site","Requests"))`, and `_timeline_svg(..., y_label="Sessions"/"Requests")`.
- `app/services/reporting/charts.py` `line_chart(points, *, width, height, y_label="", x_label="Time", y_format=None)` renders a centred `"No data"` when empty. All text escaped.
- `app/services/reporting/templates/report.html.j2` has hardcoded `<h3>` titles, `<p class="explain">…</p>`, `<p class="sample-note">…</p>`, `<td colspan="2">No data</td>`, `Total in: … Total out: …`, `Uptime: …%`, `<th>Threat</th>`, `<h2>Table of contents</h2>`. `report.css` `@page` footer uses `content:` literals ("Report generated for timezone " string(tz); "Report owner: " string(owner); "Page " counter(page) " / " counter(pages)) and the `.tz-meta` element sets the `tz`/`owner` named strings via `string-set: tz attr(data-tz), owner attr(data-owner)`.
- `app/services/reporting/service.py` `ReportService.build_html/build_report(*, tenant_name, frm, to)` → calls `build_context(...)`.

**Commands** (from `backend/`):
```
TEST_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
ADMIN_DATABASE_URL=postgresql+asyncpg://opngms:opngms@localhost:5432/opngms_test \
.venv/bin/python -m pytest -q
```

**Security:** all `t` strings are trusted constants (still autoescaped in the template; SVG stays the only `Markup`). `line_chart` keeps escaping `empty_text` + labels. `locale` is internal (default "en", unknown → en).

---

## Task 1: Weekly schedule

**Files:** Modify `app/worker.py`; Modify `tests/test_worker_reports.py`.

- [ ] **Step 1: Update the failing test** — in `tests/test_worker_reports.py`, the existing `test_enqueue_scheduled_reports_*` asserts a prior-MONTH range. Change it to assert a prior-WEEK range: `period_to` = Monday 00:00 of the current week, `period_from` = the Monday 7 days earlier, span exactly 7 days. (Compute the expected from `datetime.now(timezone.utc)` the same way `_prior_week` does, OR assert `(to - from) == timedelta(days=7)` and `from.weekday() == 0` and `to.weekday() == 0`.) Run → FAIL.

- [ ] **Step 2: Implement** — in `app/worker.py` replace `_prior_month` with:
```python
def _prior_week(now: datetime) -> tuple[datetime, datetime]:
    # [Monday 00:00 of last week, Monday 00:00 of this week)
    this_week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    prev_week_start = this_week_start - timedelta(days=7)
    return prev_week_start, this_week_start
```
Update `enqueue_scheduled_reports`: docstring "weekly report … (prior calendar week)"; `frm, to = _prior_week(datetime.now(timezone.utc))`. Update the cron in `WorkerSettings.cron_jobs`:
```python
cron(enqueue_scheduled_reports, weekday="mon", hour={4}, minute={0}),  # weekly reports, Monday ~04:00
```
(If arq's `weekday` rejects the name, use the integer for Monday — verify and comment it.)
Run Step 1 → PASS.

- [ ] **Step 3: Wording** — update `README.md` (Phase 5 row "monthly ARQ cron" → "weekly ARQ cron") and add a one-line note to `docs/superpowers/specs/2026-06-10-opngms-phase5-5E-scheduled-reports-history-design.md` that the cadence is weekly as of 5G (don't rewrite the 5E spec body).

- [ ] **Step 4: Commit**
```bash
git add app/worker.py tests/test_worker_reports.py README.md docs/superpowers/specs/2026-06-10-opngms-phase5-5E-scheduled-reports-history-design.md
git commit -m "feat(reporting): weekly scheduled reports (prior calendar week) instead of monthly"
```

---

## Task 2: Report i18n module + template/footer externalisation

**Files:** Create `app/services/reporting/i18n.py`; Modify `context.py` (thread locale + `ReportContext.t`), `service.py` (locale param), `charts.py` (`empty_text`), `templates/report.html.j2`, `templates/report.css`; Create `tests/test_report_i18n.py`; Modify `tests/test_report_context.py`.

- [ ] **Step 1: Create `app/services/reporting/i18n.py`** with the full en dict + resolver:
```python
"""Server-side report localisation. English is the only locale today; the resolver falls back to en,
so adding a language = adding a dict (no template surgery) — mirrors the frontend i18n maturity."""
from __future__ import annotations

_EN: dict[str, str] = {
    # section titles
    "attacks_title": "Attacks",
    "web_title": "Web Activity",
    "data_title": "Data Usage",
    "status_title": "Up/Down Status",
    "apps_title": "Applications",
    "webfilter_title": "Web Filter",
    "toc_title": "Table of contents",
    # explanations
    "attacks_explain": "Attempted intrusions your firewall's threat detection blocked during this period. The chart shows how many attempts occurred over time; the tables list the most frequent attack types, which of your devices were targeted, and where the attempts came from.",
    "web_explain": "The websites and online services your network looked up. The chart shows lookup volume over time; the tables show the most-visited sites, the busiest devices, and the domains that were blocked.",
    "data_explain": "How much data flowed through your firewall over time (incoming plus outgoing). The totals below summarise the whole period.",
    "status_explain": "Whether this firewall was online and reachable over the period. 'Uptime' is the share of time it was online — higher is better.",
    "apps_explain": "Applications seen on your network, each with a simple risk rating — green (Low), blue (Guarded), orange (High). These figures are sample data until application monitoring is enabled.",
    "webfilter_explain": "Categories of web content requested from your network, each with a risk rating. These figures are sample data until content categorisation is enabled.",
    "apps_sample": "Sample data — application visibility not yet ingested.",
    "webfilter_sample": "Sample data — content categorization not yet ingested.",
    # misc
    "no_data": "No data",
    "total_in": "Total in",
    "total_out": "Total out",
    "uptime": "Uptime",
    "threat": "Threat",
    "threat_low": "Low",
    "threat_guarded": "Guarded",
    "threat_high": "High",
    # ranked-table titles + columns
    "t_top_attempts": "Top Attempts",
    "t_top_targets": "Top Targets",
    "t_top_initiators": "Top Initiators",
    "t_top_sites": "Top Sites",
    "t_top_blocked": "Top Blocked",
    "t_top_detected": "Top Detected",
    "t_top_categories": "Top Categories",
    "col_signature": "Signature",
    "col_count": "Count",
    "col_target": "Target",
    "col_initiator": "Initiator",
    "col_site": "Site",
    "col_hits": "Hits",
    "col_domain": "Domain",
    "col_blocks": "Blocks",
    "col_application": "Application",
    "col_sessions": "Sessions",
    "col_category": "Category",
    "col_requests": "Requests",
    # axis labels
    "axis_time": "Time",
    "axis_attempts": "Attempts",
    "axis_dns": "DNS lookups",
    "axis_data": "Data / period",
    "axis_status": "Status",
    "axis_sessions": "Sessions",
    "axis_requests": "Requests",
    "status_up": "Up",
    "status_down": "Down",
    # footer labels
    "footer_tz": "Report generated for timezone",
    "footer_owner": "Report owner:",
    "footer_page": "Page",
    "footer_of": "/",
}

REPORT_LOCALES: dict[str, dict[str, str]] = {"en": _EN}


class ReportText:
    """Attribute/dict access to report strings (already merged with the en fallback)."""

    def __init__(self, strings: dict[str, str]) -> None:
        object.__setattr__(self, "_s", strings)

    def __getattr__(self, key: str) -> str:
        try:
            return object.__getattribute__(self, "_s")[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __getitem__(self, key: str) -> str:
        return object.__getattribute__(self, "_s")[key]


def report_text(locale: str = "en") -> ReportText:
    merged = {**_EN, **REPORT_LOCALES.get(locale, {})}  # unknown locale or partial -> en fallback
    return ReportText(merged)
```

- [ ] **Step 2: `charts.line_chart` empty_text** — add a param `empty_text: str = "No data"` and use it for the empty-state text (still escaped): change the empty branch to render `escape(empty_text)`. Existing callers default to "No data".

- [ ] **Step 3: Thread locale + `ReportContext.t`** — in `context.py`:
  - Add `t: "ReportText | None" = None` to `ReportContext` (import `ReportText` from `.i18n` at the bottom alongside the other late imports, or type as a forward ref).
  - `build_context(..., locale: str = "en")`: at the top `from app.services.reporting.i18n import report_text` (bottom import) → `t = report_text(locale)`; set `t=t` on the returned `ReportContext`.
  - This task ONLY needs `ctx.t` available + `line_chart(..., empty_text=t.no_data)` passed; the per-table/axis externalisation is Task 3 (leave the table/axis literals for now — but DO pass `empty_text=t.no_data` to every `line_chart` call).
- `service.py`: `build_html/build_report(*, tenant_name, frm, to, locale: str = "en")` → pass `locale` to `build_context`. (The generate API keeps calling with the default "en".)

- [ ] **Step 4: Template strings** — in `report.html.j2`, replace the hardcoded user-facing literals with `{{ ctx.t.X }}`:
  - `<h2>{{ ctx.t.toc_title }}</h2>`
  - `<h3>{{ ctx.t.attacks_title }}</h3>` + `<p class="explain">{{ ctx.t.attacks_explain }}</p>` (and the same for web/data/status/apps/webfilter using `*_title`/`*_explain`).
  - The two `<p class="sample-note">{{ ctx.t.apps_sample }}</p>` / `{{ ctx.t.webfilter_sample }}`.
  - `<td colspan="2">{{ ctx.t.no_data }}</td>` (both occurrences).
  - `Total in: {{ section.bandwidth.total_in }} · Total out: …` → `{{ ctx.t.total_in }}: … · {{ ctx.t.total_out }}: …`.
  - `Uptime: {{ … }}%` → `{{ ctx.t.uptime }}: {{ … }}%`.
  - `<th>Threat</th>` (both) → `<th>{{ ctx.t.threat }}</th>`.
  - Threat badge label: render the level label via `{{ ctx.t['threat_' ~ row.level] }}` (the `~` is Jinja string concat); the CSS class stays `threat-{{ row.level }}` (the raw enum). So: `<span class="threat threat-{{ row.level }}">{{ ctx.t['threat_' ~ row.level] }}</span>` (both threat tables).
  - The title-page `.tz-meta` div: extend it to also carry the footer label strings:
    `<div class="tz-meta" data-tz="{{ ctx.timezone }}" data-owner="{{ ctx.owner or '—' }}" data-ftz="{{ ctx.t.footer_tz }}" data-fowner="{{ ctx.t.footer_owner }}" data-fpage="{{ ctx.t.footer_page }}" data-fof="{{ ctx.t.footer_of }}"></div>`.

- [ ] **Step 5: Footer CSS (named strings)** — in `report.css`:
  - Extend `.tz-meta { string-set: tz attr(data-tz), owner attr(data-owner), ftz attr(data-ftz), fowner attr(data-fowner), fpage attr(data-fpage), fof attr(data-fof); }`.
  - `@bottom-left { content: string(ftz) " " string(tz); … }`
  - `@bottom-center { content: string(fowner) " " string(owner); … }`
  - `@bottom-right { content: string(fpage) " " counter(page) " " string(fof) " " counter(pages); … }`

- [ ] **Step 6: Tests** — `tests/test_report_i18n.py`: `report_text("en").attacks_title == "Attacks"`; `report_text("xx").no_data == "No data"` (unknown → en); `report_text("en")["threat_high"] == "High"`; a fake partial locale (monkeypatch/insert into `REPORT_LOCALES`) overrides one key and falls back to en for the rest. Also extend `tests/test_report_context.py` to assert the rendered HTML still contains the section titles + an explanation (now sourced from `ctx.t`) and that `ctx.t` is set. Run targeted + full suite green.

- [ ] **Step 7: Commit**
```bash
git add app/services/reporting/i18n.py app/services/reporting/charts.py app/services/reporting/context.py \
        app/services/reporting/service.py app/services/reporting/templates/report.html.j2 \
        app/services/reporting/templates/report.css tests/test_report_i18n.py tests/test_report_context.py
git commit -m "feat(reporting): server-side report i18n layer (en + fallback); externalise template + footer strings"
```

---

## Task 3: Externalise the data-layer strings (tables + axis labels)

**Files:** Modify `app/services/reporting/context.py`, `app/services/reporting/mock_sections.py`; Modify `tests/test_report_context.py` / `tests/test_report_mock_sections.py`.

- [ ] **Step 1: `context.py`** — using the `t = report_text(locale)` already built in `build_context`, replace the hardcoded `RankedTable(...)` titles/columns and the `line_chart` axis labels with `t.*`:
  - Attacks: `RankedTable(t.t_top_attempts, (t.col_signature, t.col_count), …)`, `RankedTable(t.t_top_targets, (t.col_target, t.col_count), …)`, `RankedTable(t.t_top_initiators, (t.col_initiator, t.col_count), …)`; attacks timeline `y_label=t.axis_attempts, x_label=t.axis_time, empty_text=t.no_data`.
  - Web Activity: `t.t_top_sites/(t.col_site,t.col_hits)`, `t.t_top_initiators/(t.col_initiator,t.col_hits)`, `t.t_top_blocked/(t.col_domain,t.col_blocks)`; timeline `y_label=t.axis_dns`.
  - Data Usage timeline `y_label=t.axis_data`, `y_format=human_bytes`.
  - Up/Down timeline `y_label=t.axis_status`; the updown formatter must use `t.status_up`/`t.status_down` — build it inline: `def _ud(v): return t.status_up if v >= 0.99 else (t.status_down if v <= 0.01 else "")` and pass `y_format=_ud`. (Remove/keep the module-level `_updown_fmt`; the locale-aware closure replaces it in build_context.)
- [ ] **Step 2: `mock_sections.py`** — pass `t` (or `locale`) into `applications_block`/`web_filter_block` and build the table titles/columns + `_timeline_svg` axis labels from `t` (`t.t_top_detected`/`t.col_application`/`t.col_sessions`, `t.t_top_blocked`/`t.col_blocks`, `t.t_top_categories`/`t.col_category`/`t.col_sessions|requests`, `t.t_top_initiators`/`t.col_initiator`, `t.t_top_sites`/`t.col_site`/`t.col_requests`, `axis_sessions`/`axis_requests`, `x_label=t.axis_time`, `empty_text=t.no_data`). `build_context` calls `applications_block(dev.name, t)` / `web_filter_block(dev.name, t)`.
- [ ] **Step 3: Tests** — update `tests/test_report_mock_sections.py` for the new signature (pass a `report_text("en")`); keep the determinism asserts. Update any `test_report_context.py` asserts that referenced the old literals (they still read "Top Attempts" etc. since en is unchanged). Run full suite green.
- [ ] **Step 4: Commit**
```bash
git add app/services/reporting/context.py app/services/reporting/mock_sections.py \
        tests/test_report_context.py tests/test_report_mock_sections.py
git commit -m "feat(reporting): externalise ranked-table + axis strings to the report i18n layer"
```

---

## Task 4: Technical debt
- [ ] **Step 1:** Append 5G debt: only the **en** locale exists (adding a language = add a dict to `REPORT_LOCALES` + wire `locale` selection); a **per-tenant `report_settings.language`** + a settings-UI picker is the next step (so an operator selects the locale; the engine already threads `locale`); date/number formatting is not localised yet.
- [ ] **Step 2:** Commit `docs: technical debt milestone 5G`.

---

## Definition of "Done" (5G)
- Scheduled reports run **weekly** (prior calendar week); no "monthly/month" wording remains for the schedule.
- Every user-facing report string comes from `report_text(locale)`; en renders today's text; an unknown locale falls back to en; adding a locale = adding a dict. All escaped/secret-safe; backend suite green; a rendered sample still looks right.
