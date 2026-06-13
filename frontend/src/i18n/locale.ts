// Locale set, native labels, and detection/persistence helpers.
//
// The UI language is a per-operator preference stored in localStorage (no backend
// involvement) — independent of the per-tenant report language. To add a language:
// add its code here, a matching label, and a sibling dictionary wired in ./index.ts.

export const SUPPORTED_LOCALES = [
  "en", "it", "es", "fr", "de", "pt", "nl", "ru", "ar", "zh", "zh-TW", "ja",
] as const;

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
  ru: "Русский",
  ar: "العربية",
  zh: "简体中文",
  "zh-TW": "繁體中文",
  ja: "日本語",
};

// Right-to-left locales — the app flips layout direction for these (see DirectionSync).
const RTL_LOCALES = new Set<Locale>(["ar"]);

/** Whether the given locale uses a right-to-left script. */
export function isRtl(locale: Locale): boolean {
  return RTL_LOCALES.has(locale);
}

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
    const supportedLower = SUPPORTED_LOCALES.map((l) => l.toLowerCase());
    for (const raw of langs) {
      if (!raw) continue;
      const lang = raw.toLowerCase();
      // Exact tag match first (e.g. `zh-TW` → `zh-TW`).
      const exact = supportedLower.indexOf(lang);
      if (exact >= 0) return SUPPORTED_LOCALES[exact];
      // Chinese: pick Traditional vs Simplified by script/region; default Simplified.
      if (lang.startsWith("zh")) {
        return /hant|tw|hk|mo/.test(lang) ? "zh-TW" : "zh";
      }
      // Otherwise match by primary subtag (e.g. `fr-FR` → `fr`).
      const prefix = supportedLower.indexOf(lang.split("-")[0]);
      if (prefix >= 0) return SUPPORTED_LOCALES[prefix];
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
