import {
  createContext,
  createElement,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { en, type Dict } from "./en";
import {
  DEFAULT_LOCALE,
  detectInitialLocale,
  isLocale,
  LOCALE_LABELS,
  type Locale,
  persistLocale,
  SUPPORTED_LOCALES,
} from "./locale";

export type { Dict } from "./en";
export type { Locale } from "./locale";

// `en` is bundled eagerly (the default locale, the fallback, and the source of the `Dict` type). The
// other locales — each typed `: Dict`, so `tsc -b` still enforces key parity against en — load on demand
// via dynamic `import()`, so each becomes its own chunk and stays out of the main bundle. Fetched dicts
// are cached for the session.
const loaders: Record<Exclude<Locale, "en">, () => Promise<Dict>> = {
  it: () => import("./it").then((m) => m.it),
  es: () => import("./es").then((m) => m.es),
  fr: () => import("./fr").then((m) => m.fr),
  de: () => import("./de").then((m) => m.de),
  pt: () => import("./pt").then((m) => m.pt),
  nl: () => import("./nl").then((m) => m.nl),
  ru: () => import("./ru").then((m) => m.ru),
  ar: () => import("./ar").then((m) => m.ar),
  zh: () => import("./zh").then((m) => m.zh),
  "zh-TW": () => import("./zhTW").then((m) => m.zhTW),
  ja: () => import("./ja").then((m) => m.ja),
};
const cache: Partial<Record<Locale, Dict>> = { en };

interface I18nState {
  locale: Locale;
  t: Dict;
  setLocale: (locale: Locale) => void;
}

const I18nContext = createContext<I18nState>({
  locale: DEFAULT_LOCALE,
  t: en,
  setLocale: () => {},
});

export function I18nProvider({
  children,
  locale: localeOverride,
}: {
  children: ReactNode;
  locale?: Locale;
}) {
  // The app self-detects the locale (localStorage → browser → default). Tests may pass an
  // explicit `locale` prop to pin the language deterministically.
  const [locale, setLocaleState] = useState<Locale>(() => localeOverride ?? detectInitialLocale());
  const effective = localeOverride ?? locale;

  // The active dictionary is DERIVED during render from the cache: the cached dict for the active locale,
  // or `en` while a non-en locale's chunk is still loading. The counter only re-renders once an async
  // dict has landed in the cache (so we avoid a synchronous setState inside the loader effect).
  const [, onLoaded] = useState(0);
  const dict = cache[effective] ?? en;

  const setLocale = useCallback((next: Locale) => {
    if (!isLocale(next)) return;
    persistLocale(next);
    setLocaleState(next);
  }, []);

  // Reflect the active language on <html lang> for accessibility / screen readers.
  useEffect(() => {
    document.documentElement.lang = effective;
  }, [effective]);

  // Load the active locale's dictionary on demand (cached after the first fetch). `en` is always cached,
  // so the common case loads nothing; switching to another locale briefly shows `en` until its chunk lands.
  useEffect(() => {
    if (cache[effective]) return;
    let cancelled = false;
    void loaders[effective as Exclude<Locale, "en">]().then((d) => {
      cache[effective] = d;
      if (!cancelled) onLoaded((n) => n + 1);
    });
    return () => {
      cancelled = true;
    };
  }, [effective]);

  const value = useMemo<I18nState>(
    () => ({ locale: effective, t: dict, setLocale }),
    [effective, dict, setLocale],
  );
  return createElement(I18nContext.Provider, { value }, children);
}

// Returns the active dictionary so components can read strings as `t.feature.key`.
export function useT(): Dict {
  return useContext(I18nContext).t;
}

// Locale state for the language switcher: the active locale, a setter, and the option list.
export function useLocale(): {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  locales: { value: Locale; label: string }[];
} {
  const { locale, setLocale } = useContext(I18nContext);
  const locales = useMemo(
    () => SUPPORTED_LOCALES.map((l) => ({ value: l, label: LOCALE_LABELS[l] })),
    [],
  );
  return { locale, setLocale, locales };
}
