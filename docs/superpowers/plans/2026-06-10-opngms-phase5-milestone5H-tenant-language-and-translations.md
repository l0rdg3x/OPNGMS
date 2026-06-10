# OPNGMS — Phase 5 / Milestone 5H: Per-Tenant Report Language + Translations — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`).

**Goal:** Let a tenant admin pick the report **language** (a `report_settings.language` field + a settings-UI selector; the engine renders in that locale), and add **complete translations** for it/es/fr/de/pt/nl.

**Architecture:** `report_settings.language` (migration `0013`, default `'en'`) drives `ReportService.build_report`'s locale (covers on-demand + scheduled). `i18n.py` gains the translation dicts + `LANGUAGE_NAMES`/`available_locales()`; a `GET /reports/languages` endpoint lists them; the settings page gains a Language `Select`.

**Tech Stack:** Python 3.12+, SQLAlchemy/Alembic, FastAPI; React + Mantine; pytest, Vitest.

---

## Context for the implementer (read first)

- **i18n** `app/services/reporting/i18n.py`: `_EN` (the en key set), `REPORT_LOCALES = {"en": _EN}`, `ReportText`, `report_text(locale)` (merges `{**_EN, **REPORT_LOCALES.get(locale, {})}` → en fallback).
- **report_settings** (5D): `app/models/report_settings.py` (`tenant_id` PK, `title`/`owner`/`timezone`, `logo`/`logo_mime`, `updated_at`); `app/repositories/report_settings.py` (`get`/`get_or_default`/`upsert(*, title, owner, timezone)`/`set_logo`/`clear_logo`); `app/schemas/report_settings.py` (`ReportSettingsIn{title,owner,timezone}`, `ReportSettingsOut{title,owner,timezone,has_logo,logo_mime}`); `app/api/reports.py` (`GET/PUT /reports/settings`, `_settings_to_out`). `ReportService.build_html/build_report(*, tenant_name, frm, to, locale="en")` loads `settings = get_or_default()` for branding.
- **Migration** pattern: `migrations/versions/0011_report_settings.py` / `0012_*`. For an ADD COLUMN use `op.add_column("report_settings", sa.Column("language", sa.String(), nullable=False, server_default="en"))`; down `op.drop_column`. No RLS change (table already RLS).

**Commands** (backend): `cd backend && TEST_DATABASE_URL=... ADMIN_DATABASE_URL=... .venv/bin/python -m pytest -q` + `alembic check`. Frontend: `cd frontend && npm test/build/lint`, `npm run gen:api`.

---

## Task 1: Backend — `report_settings.language` + languages API + wiring

**Files:** Modify `app/models/report_settings.py`, `migrations/versions/0013_report_settings_language.py` (new), `app/repositories/report_settings.py`, `app/schemas/report_settings.py`, `app/api/reports.py`, `app/services/reporting/i18n.py`, `app/services/reporting/service.py`; Tests `tests/test_report_settings_model.py`, `tests/test_report_settings_api.py`.

- [ ] **Step 1: i18n metadata** — in `i18n.py` add (after `REPORT_LOCALES`):
```python
LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "it": "Italiano", "es": "Español", "fr": "Français",
    "de": "Deutsch", "pt": "Português", "nl": "Nederlands",
}


def available_locales() -> list[tuple[str, str]]:
    # (code, display name) for every locale that has a dict, en first.
    codes = sorted(REPORT_LOCALES.keys(), key=lambda c: (c != "en", c))
    return [(c, LANGUAGE_NAMES.get(c, c)) for c in codes]
```
(Task 2 adds the it/es/… dicts to `REPORT_LOCALES`; until then `available_locales()` returns just en.)

- [ ] **Step 2: Model + migration** — add to `ReportSettings`: `language: Mapped[str] = mapped_column(String, default="en", server_default="en")`. Create `migrations/versions/0013_report_settings_language.py` (`down_revision="0012"`): `op.add_column("report_settings", sa.Column("language", sa.String(), nullable=False, server_default="en"))`; `downgrade` drops it. Run `alembic check` clean.

- [ ] **Step 3: Repo** — `upsert(*, title, owner, timezone, language="en")` sets `row.language`.

- [ ] **Step 4: Schema** — `ReportSettingsIn` add `language: str = "en"`; `ReportSettingsOut` add `language: str`. `_settings_to_out` includes `language=settings.language`.

- [ ] **Step 5: API** — in `app/api/reports.py`:
  - `update_report_settings` (PUT): validate `body.language` is a known locale — `from app.services.reporting.i18n import REPORT_LOCALES`; `if body.language not in REPORT_LOCALES: raise HTTPException(400, "unsupported language")`. Pass `language=body.language` to `upsert`; add to the audit details.
  - New `GET /reports/languages` (`require_tenant(Action.DEVICE_VIEW)`) → `from app.services.reporting.i18n import available_locales`; return `[{"code": c, "name": n} for c, n in available_locales()]`. (Use a small pydantic `ReportLanguageOut{code:str,name:str}` or return a list of dicts with a `response_model=list[dict]`.)

- [ ] **Step 6: Service uses settings.language** — change `ReportService.build_html/build_report` so the locale comes from settings: signature `locale: str | None = None`; after loading `settings`, `effective = locale or settings.language or "en"`; pass `locale=effective` to `build_context`. (Both the on-demand API and the worker call `build_report` → both honour the tenant's language.)

- [ ] **Step 7: Tests** — `tests/test_report_settings_model.py`: default `language == "en"`. `tests/test_report_settings_api.py`: GET settings returns `language`; PUT with `language:"it"` (once Task 2 adds it — for Task 1 test with `"en"` and an invalid `"xx"`→400) updates it; `GET /reports/languages` returns at least `{"code":"en"}`. (After Task 2, extend to assert it/es/… appear and a report renders in a non-en locale via the service.) Full suite green.

- [ ] **Step 8: Commit**
```bash
git add app/models/report_settings.py migrations/versions/0013_report_settings_language.py \
        app/repositories/report_settings.py app/schemas/report_settings.py app/api/reports.py \
        app/services/reporting/i18n.py app/services/reporting/service.py \
        tests/test_report_settings_model.py tests/test_report_settings_api.py
git commit -m "feat(reporting): per-tenant report language (report_settings.language) + languages API + wiring"
```

---

## Task 2: Translations (it/es/fr/de/pt/nl)

**Authored by the orchestrator** (translation accuracy). Add a complete dict for each locale to `REPORT_LOCALES` in `i18n.py` — every `_EN` key translated naturally (Firewall/DNS kept where idiomatic). Add a test `tests/test_report_i18n.py::test_all_locales_complete` asserting **each locale has exactly the `_EN` key set** (`set(d) == set(_EN)` for every locale) so no key is missing/extra, plus a render smoke (`report_text("it").attacks_title != report_text("en").attacks_title`). Commit `feat(reporting): full it/es/fr/de/pt/nl report translations`.

---

## Task 3: Frontend — Language selector in report settings

**Files:** Regen `src/api/schema.d.ts`; Modify `src/reports/settingsHooks.ts` (a `useReportLanguages()` hook), `src/pages/ReportSettingsPage.tsx` (a Language `Select`), `src/i18n/en.ts`; Test `src/pages/__tests__/reportsettings.test.tsx`.

- [ ] **Step 1:** `npm run gen:api`; add `useReportLanguages()` (GET `/api/tenants/{tenant_id}/reports/languages`, tenant-scoped) to `settingsHooks.ts`. `ReportSettingsIn`/`Out` now include `language`.
- [ ] **Step 2:** In `ReportSettingsPage`, add a Mantine `Select` (label `t.reports.settings.language`) whose `data` = `useReportLanguages().data` mapped to `{value: code, label: name}`, bound to the form's `language` (init from the GET settings, default `"en"`), saved by the existing update mutation (add `language` to the PUT body). i18n key.
- [ ] **Step 3:** Tests: the Select renders with the languages from a mocked `/reports/languages`; saving sends `language` in the PUT body; default `en`. `npm test`/`build`/`lint` clean.
- [ ] **Step 4:** Commit `feat(fe): report language selector in report settings`.

---

## Task 4: Technical debt
- Append 5H debt (date/number formatting not localised; no RTL; auto-detect not done) and commit.

---

## Definition of "Done" (5H)
- A tenant admin selects the report language; on-demand + scheduled reports render in it; `GET /reports/languages` lists the options; it/es/fr/de/pt/nl are complete (== en key set) with en fallback for anything missing. Backend + frontend suites green; migration clean.
