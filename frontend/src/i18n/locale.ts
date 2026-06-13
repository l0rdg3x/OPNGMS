// Locale set, native labels, and detection/persistence helpers.
//
// The UI language is a per-operator preference stored in localStorage (no backend
// involvement) — independent of the per-tenant report language. To add a language:
// add its code here, a matching label, and a sibling dictionary wired in ./index.ts.

export const SUPPORTED_LOCALES = ["en", "it", "es", "fr", "de", "pt", "nl"] as const;

export type Locale = (typeof SUPPORTED_LOCALES)[number];

// Native language names shown in the language switcher.
export const LOCALE_LABELS: Record<Locale, string> = {
  en: "English",
  it: "Italiano",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  pt: "Português",
  nl: "Nederlands",
};

export const DEFAULT_LOCALE: Locale = "en";

const LS_KEY = "opngms.locale";

/** Narrow an arbitrary string to a supported Locale. */
export function isLocale(value: string | null | undefined): value is Locale {
  return !!value && (SUPPORTED_LOCALES as readonly string[]).includes(value);
}

/**
 * Resolve the initial locale: a persisted choice wins, else the browser's
 * preferred language (by prefix, e.g. `it-IT` → `it`), else the default.
 */
export function detectInitialLocale(): Locale {
  try {
    const stored = localStorage.getItem(LS_KEY);
    if (isLocale(stored)) return stored;
  } catch {
    // storage unavailable (private browsing) — fall through to detection
  }
  try {
    const langs = navigator.languages?.length ? navigator.languages : [navigator.language];
    for (const lang of langs) {
      const prefix = lang?.toLowerCase().split("-")[0];
      if (isLocale(prefix)) return prefix;
    }
  } catch {
    // navigator unavailable — fall through to default
  }
  return DEFAULT_LOCALE;
}

/** Persist the chosen locale (silently ignores storage errors). */
export function persistLocale(locale: Locale): void {
  try {
    localStorage.setItem(LS_KEY, locale);
  } catch {
    // storage quota or private-browsing restriction — ignore
  }
}
