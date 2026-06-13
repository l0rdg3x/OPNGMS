# Frontend UI Localization (i18n) — Design

**Date:** 2026-06-13
**Status:** Approved

## Goal

Localize the OPNGMS frontend UI into the **same 7 languages as the PDF reports** —
English, Italian, Spanish, French, German, Portuguese, Dutch (`en`, `it`, `es`, `fr`,
`de`, `pt`, `nl`) — selectable per operator, persisted in `localStorage`, with **zero
backend changes**.

## Background — current state

The frontend already has a small **custom React Context** i18n (`frontend/src/i18n/`):

- `en.ts` — `export const en = { … } as const; export type Dict = typeof en;`
  389 leaf strings across 23 top-level namespaces (incl. a `catalog:` block for the
  config editor's chrome).
- `index.ts` — `I18nProvider` (currently fixed to `en`), `useT(): Dict` returning the
  active dictionary; components read strings as direct property access `t.feature.key`.
- `type Locale = "en"`; `dictionaries: Record<Locale, Dict> = { en }`.

There is no pluralization/interpolation engine: dynamic values are composed in components,
so translating is a **pure per-key value replacement** (no format-string risk — confirmed
by the translation audit).

The established pattern for a persisted, switchable selection is
`tenant/TenantProvider.tsx` (localStorage + context setter) + `components/TenantSwitcher.tsx`
(Mantine `Select`). The locale feature mirrors it.

## Design

### 1. Type model — key parity enforced at compile time

`en` is declared `as const`, so `type Dict = typeof en` has **literal** value types
(`logout: "Log out"`). A translated value (`"Esci"`) is **not** assignable to that literal,
so sibling dictionaries cannot be typed against it directly.

Widen the value leaves to `string` while preserving the exact key structure:

```ts
// en.ts
type Localized<T> = { [K in keyof T]: T[K] extends string ? string : Localized<T[K]> };
export type Dict = Localized<typeof en>;
```

Each sibling dictionary is annotated `: Dict`, e.g. `export const it: Dict = { … }`. This
makes **`tsc -b` (the build gate) enforce key parity in both directions** — a missing key
or an extra/renamed key fails the build — while accepting any translated string value.
`en` (still `as const`) remains assignable to the widened `Dict`. `useT()` keeps returning
`Dict`; components now see `string` leaves (they already use them as strings).

### 2. Locale set + metadata

`i18n/locale.ts`:

```ts
export const SUPPORTED_LOCALES = ["en", "it", "es", "fr", "de", "pt", "nl"] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English", it: "Italiano", es: "Español", fr: "Français",
  de: "Deutsch", pt: "Português", nl: "Nederlands",
};
export const DEFAULT_LOCALE: Locale = "en";
```

`Locale` moves out of `index.ts` into `locale.ts` so the metadata and the union live
together. `index.ts` re-exports `Locale` for backwards compatibility.

### 3. Stateful provider + persistence

`index.ts` becomes stateful, mirroring `TenantProvider`:

- `detectInitialLocale()` — `localStorage["opngms.locale"]` (if a supported value) →
  else first supported match of `navigator.language` prefix (e.g. `it-IT` → `it`) →
  else `DEFAULT_LOCALE`. Wrapped in try/catch (private browsing).
- `persistLocale(locale)` — writes `localStorage["opngms.locale"]`, try/catch.
- `I18nProvider` holds `locale` in `useState(detectInitialLocale)`; `setLocale` persists +
  updates state. An effect sets `document.documentElement.lang = locale` for accessibility.
- `useLocale(): { locale, setLocale, locales }` — new hook (`locales` = the
  `{ value, label }` list for the switcher). `useT()` is unchanged.
- `I18nProvider` keeps an **optional `locale` prop override** so existing tests that render
  `<I18nProvider>` keep defaulting to `en` deterministically.

### 4. Dictionaries

Six new sibling files (`it.ts`, `es.ts`, `fr.ts`, `de.ts`, `pt.ts`, `nl.ts`), each
structurally identical to `en.ts` with only the string values translated, typed `: Dict`.
Translations are **machine-generated** (model), with terminology aligned to the existing
backend report locale dictionaries (`backend/app/services/reporting/i18n.py`) for
consistency with the PDFs (e.g. "Attacks", "Up/Down status"). A native-speaker review is
recommended for production polish but is out of scope here.

### 5. Switcher UI

`components/LanguageSwitcher.tsx` — a compact Mantine `Select` listing the native language
names (from `useLocale().locales`), `value = locale`, `onChange = setLocale`,
`allowDeselect={false}`. Placed in:

- the **AppShell header** (right group, beside the email/logout), and
- the **LoginPage** (so the language can be changed before authenticating).

### 6. Scope boundaries (YAGNI)

- **No backend changes.** UI language is per-operator (localStorage) and is **independent of
  the per-tenant report locale** (set under Report settings).
- **Config-editor field labels** come from device introspection (the backend catalog), not
  this dictionary — they remain as provided by OPNsense; only the editor chrome (`catalog:`
  block) is localized.
- **No plural/interpolation engine** — the existing static-string model is kept.

## Testing

- **Parity test** (`i18n/__tests__`): every dictionary in `dictionaries` has the exact same
  set of (deep) keys as `en` — a runtime backstop in addition to the `tsc -b` gate.
- **Detect/persist test**: `detectInitialLocale()` honors localStorage, then
  `navigator.language`, then falls back to `en`; `setLocale` persists.
- **Switcher test**: changing the switcher swaps the active strings (render a known key in
  two locales).
- **Build gate**: `npm run build` (`tsc -b` + vite) is the completeness backstop; `npm test`
  and `npm run lint` must stay green.

## Files

- Modify: `frontend/src/i18n/en.ts` (widen `Dict`)
- Create: `frontend/src/i18n/locale.ts` (locale set + labels + detect/persist)
- Modify: `frontend/src/i18n/index.ts` (stateful provider, `useLocale`, `<html lang>`)
- Create: `frontend/src/i18n/{it,es,fr,de,pt,nl}.ts` (translated dictionaries)
- Create: `frontend/src/components/LanguageSwitcher.tsx`
- Modify: `frontend/src/components/AppShell.tsx`, `frontend/src/pages/LoginPage.tsx`
- Create: `frontend/src/i18n/__tests__/i18n.test.tsx`
