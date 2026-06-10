# OPNGMS — Phase 5 / Milestone 5G: Weekly Schedule + Translatable Report Content — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user asked for a weekly cadence and for the report's text to be translatable)
- **Phase:** 5 — Milestone 5G (corrections on top of the completed reporting feature)
- **Depends on:** 5A–5F in `main`
- **Enables:** weekly customer reports; report content ready for localisation (like the frontend i18n)

## 1. Context & goal

Two corrections from the user:
1. The scheduled report cadence in code is **monthly** (`_prior_month`, cron `day=1`) — it should be
   **weekly** (the prior calendar week). Fix the cadence + all "monthly/month" wording.
2. The report's text is currently **hardcoded English** in the template/context/charts. It must be
   **translatable** — externalised into a server-side locale layer (en dict + resolver, fallback to en,
   ready for more locales), exactly like the frontend i18n (which keeps an English dict ready for locales).

## 2. Design decisions (5G)

| Topic | Decision |
|-------|----------|
| Schedule | ARQ cron runs **weekly** (Monday ~04:00) and generates the **prior calendar week** `[prev Monday 00:00, this Monday 00:00)`. `_prior_month` → `_prior_week`. Update worker comments + README + the 5E spec note. |
| Report i18n | New `app/services/reporting/i18n.py`: `REPORT_LOCALES = {"en": {...}}` (every report string keyed) + `report_text(locale="en") -> ReportText` (an object exposing keys as attributes, **falling back to en** for an unknown locale/key). The frontend has no language picker yet either — this mirrors its "en dict, ready for more locales" maturity. |
| Locale threading | `build_context(..., locale="en")` builds `t = report_text(locale)` and attaches it as `ctx.t`; `ReportService.build_report(..., locale="en")` passes it through. The template + context + mock provider + charts read strings from `t` (no hardcoded user-facing literals). |
| Externalised strings | Section titles (Attacks/Web Activity/Data Usage/Up-Down Status/Applications/Web Filter), the 6 explanations, the 2 "Sample data — …" notes, "Table of contents", "No data", "Total in/out", "Uptime", the "Threat" header + the Low/Guarded/High level labels, the ranked-table titles + column headers (built in `context.py`/`mock_sections.py`), the timeline **axis labels** (Time/Attempts/DNS lookups/Data per period/Status/Up/Down/Sessions/Requests), and the **footer** labels (timezone/owner/page). |
| Footer (CSS) | The footer label prefixes live in `report.css` `@page` `content:`. Translate them via WeasyPrint **named strings** set from hidden HTML (extend the existing `.tz-meta` `string-set` that already sets `tz`/`owner`): set `footer_tz_label`/`footer_owner_label`/`footer_page_label`/`footer_of_label` from `ctx.t` and reference `string(...)` in the CSS. |
| Selection (deferred) | A **per-tenant `language`** on `report_settings` + a settings-UI picker is the natural next step (so an operator selects the locale) — **deferred** (no non-en locale exists yet, and the frontend has no picker either). `locale` defaults to `"en"` everywhere; the plumbing is ready. Recorded as tech debt. |

## 3. Components

- **`app/worker.py`**: `_prior_week(now)` + `enqueue_scheduled_reports` uses it; cron → `cron(enqueue_scheduled_reports, weekday="mon", hour={4}, minute={0})`; comments say "weekly".
- **`app/services/reporting/i18n.py`** (new): the en locale dict + `report_text(locale)` resolver (a `ReportText` with attribute access + en fallback).
- **`context.py`**: `build_context(..., locale="en")` → `t = report_text(locale)`, set `ReportContext.t`; build the ranked-table titles/columns + axis labels from `t`; pass `t.no_data` to `line_chart`.
- **`mock_sections.py`**: take a `t` (or locale) and build the mock table titles/columns/axis labels from `t`.
- **`charts.line_chart(..., empty_text="No data")`**: the empty-state text becomes a param (context passes `t.no_data`); still escaped.
- **`templates/report.html.j2` + `report.css`**: replace every hardcoded user-facing literal with `{{ ctx.t.X }}`; footer labels via named strings.
- **`service.py`**: `build_report`/`build_html` accept `locale="en"`, pass it to `build_context`.

## 4. Security & safety
- No data/aggregation change. All `t` strings are trusted constants from our locale dict (still rendered
  through autoescape; only the generated SVG stays `Markup`). `line_chart` still escapes `empty_text` +
  all labels. The `locale` is an internal selector (defaults "en", unknown → en fallback) — never a
  security surface.

## 5. Milestone 5G breakdown (for the plan)
1. **Weekly schedule**: `_prior_week` + cron `weekday="mon"` + wording (worker/README/5E spec) + worker tests (job + the prior-week range Mon→Mon, incl. a Sunday/Monday boundary).
2. **Report i18n core + template**: `i18n.py` (en dict + resolver) + thread `locale`/`ctx.t` through `build_context`/`ReportService`; externalise the **template** strings (titles, explanations, sample-notes, "No data", Total/Uptime, Threat header + level labels, TOC) + the **footer** named-string labels + `line_chart(empty_text=…)`; tests (en renders the externalised strings; an unknown locale falls back to en; switching a key in a fake locale changes the output).
3. **Data-layer strings**: externalise the ranked-table titles + column headers (in `context.py`) and the mock titles/columns + axis labels (in `mock_sections.py`) into the en dict; tests.
4. **Tech debt** (incl. the deferred per-tenant `language` field + UI + adding a real locale).

## 6. Definition of "Done" (5G)
- Scheduled reports run **weekly** (prior calendar week); no "monthly/month" wording remains for the
  schedule.
- Every user-facing report string (titles, explanations, table headers, axis labels, footer, misc) comes
  from the locale layer; `report_text("en")` renders today's English; an unknown locale falls back to en;
  adding a locale = adding a dict (no template surgery). All escaped/secret-safe; backend suite green; a
  rendered sample still looks right.

## 7. Non-goals
- Adding an actual non-English locale (only the en dict + the plumbing now).
- A per-tenant language picker (`report_settings.language` + UI) — deferred to a follow-up.
- Localising numbers/dates formatting (kept as-is for now).
