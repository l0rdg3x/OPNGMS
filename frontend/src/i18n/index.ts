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
import { ar } from "./ar";
import { de } from "./de";
import { en, type Dict } from "./en";
import { es } from "./es";
import { fr } from "./fr";
import { it } from "./it";
import { ja } from "./ja";
import { nl } from "./nl";
import { pt } from "./pt";
import { ru } from "./ru";
import { zh } from "./zh";
import { zhTW } from "./zhTW";
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

// Every supported locale's dictionary. Each is typed `: Dict`, so `tsc -b` guarantees
// they all share en's exact key structure.
const dictionaries: Record<Locale, Dict> = {
  en, it, es, fr, de, pt, nl, ru, ar, zh, "zh-TW": zhTW, ja,
};

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

  const setLocale = useCallback((next: Locale) => {
    if (!isLocale(next)) return;
    persistLocale(next);
    setLocaleState(next);
  }, []);

  // Reflect the active language on <html lang> for accessibility / screen readers.
  useEffect(() => {
    document.documentElement.lang = effective;
  }, [effective]);

  const value = useMemo<I18nState>(
    () => ({ locale: effective, t: dictionaries[effective] ?? en, setLocale }),
    [effective, setLocale],
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
