# OPNGMS — Phase 5 / Milestone 5H: Per-Tenant Report Language + Translations — Design Spec

- **Date:** 2026-06-10
- **Status:** Approved (the user asked to do the per-tenant language selection, then add translations for every language I can translate)
- **Phase:** 5 — Milestone 5H (builds on the 5G report i18n layer)
- **Depends on:** 5D (report_settings + settings UI), 5G (report i18n `report_text(locale)`) in `main`
- **Enables:** customers receive reports in their language

## 1. Context & goal

5G externalised every report string into a server-side locale layer (en, fallback to en, ready for more
locales). 5H: (A) let a **tenant admin pick the report language** (a `language` on `report_settings` + a
settings-UI selector; the engine renders in that locale), and (B) **add full translations** for the
languages I can translate well.

## 2. Design decisions (5H)

| Topic | Decision |
|-------|----------|
| Tenant language | New `report_settings.language` (TEXT, default `'en'`). `ReportService.build_report` uses `settings.language` as the report locale (covers both the on-demand API and the scheduled worker, which both go through `build_report`). |
| Available locales | `app/services/reporting/i18n.py` gains `LANGUAGE_NAMES = {"en":"English", …}` + `available_locales()`; a `GET /reports/languages` endpoint (DEVICE_VIEW) returns `[{code,name}]` for the UI. The PUT settings validates `language ∈ available` → 400 otherwise. |
| Translations | Add **complete** locale dicts (every `_EN` key) for: **it, es, fr, de, pt, nl** (Italian, Spanish, French, German, Portuguese, Dutch). Each is a full, natural translation; technical terms (Firewall, DNS) kept where idiomatic. A test enforces **every locale has exactly the en key set** (no missing/extra keys). |
| Selection UI | The 5D report-settings page gains a **Language `Select`** (options from `GET /reports/languages`), saved via the existing PUT settings. |
| Fallback | Unchanged: an unknown/partial locale falls back to en per key (so even a partial future locale is safe). |

## 3. Backend

- **Model/migration**: `report_settings.language` (migration `0013` ALTER TABLE ADD COLUMN, default `'en'`); model field; repo `upsert(..., language=)`.
- **i18n**: full `it/es/fr/de/pt/nl` dicts in `REPORT_LOCALES`; `LANGUAGE_NAMES`; `available_locales() -> list[tuple[str,str]]`.
- **Service**: `ReportService.build_report/build_html` source the locale from `settings.language` (the existing `locale` param becomes an optional override; default → settings).
- **API** (`app/api/reports.py`): `ReportSettingsIn`/`Out` gain `language`; PUT validates it; `GET /reports/languages` (DEVICE_VIEW) lists the available locales. Audit/CSRF/RBAC unchanged.

## 4. Frontend

- The report-settings page (`ReportSettingsPage`) gains a **Language `Select`** populated from a
  `useReportLanguages()` hook (GET `/reports/languages`), bound to the `language` field, saved with the
  rest via the existing update mutation. i18n for the label.

## 5. Security & safety
- `language` is validated against the known locale set on write (400 otherwise); the resolver also falls
  back to en, so a stored unknown value can never crash rendering. No new data/secret surface. Migration
  is a nullable→defaulted column add (RLS on `report_settings` already in place — unchanged).

## 6. Milestone 5H breakdown (for the plan)
1. **Backend language field + wiring + languages API**: migration `0013` + model/repo/schema; `ReportService` uses `settings.language`; `LANGUAGE_NAMES`/`available_locales`; `GET /reports/languages`; PUT validation; tests.
2. **Translations**: full `it/es/fr/de/pt/nl` dicts in `REPORT_LOCALES` + a "all locales complete (== en key set)" test + a render smoke for a non-en locale.
3. **Frontend**: Language `Select` in the report-settings page + hook + i18n + tests.
4. **Tech debt** + a final render in a non-en locale.

## 7. Definition of "Done" (5H)
- A tenant admin selects the report language in settings; generated reports (on-demand + scheduled) render
  in that language; `GET /reports/languages` lists the options.
- it/es/fr/de/pt/nl have complete translations (every en key); a partial/unknown locale still falls back to
  en. Backend + frontend suites green; migration applies cleanly.

## 8. Non-goals
- Localising date/number formatting (the strings translate; numbers/dates stay as-is).
- Auto-detecting the recipient's language; RTL languages (the chosen set is LTR).
