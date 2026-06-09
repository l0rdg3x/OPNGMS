import { createContext, createElement, type ReactNode, useContext, useMemo } from "react";
import { en, type Dict } from "./en";

export type { Dict } from "./en";

// Supported locales. Add an entry here (and a matching dictionary file) to add a language.
export type Locale = "en";

const dictionaries: Record<Locale, Dict> = { en };

const DEFAULT_LOCALE: Locale = "en";

interface I18nState {
  locale: Locale;
  t: Dict;
}

const I18nContext = createContext<I18nState>({ locale: DEFAULT_LOCALE, t: en });

export function I18nProvider({
  children,
  locale = DEFAULT_LOCALE,
}: {
  children: ReactNode;
  locale?: Locale;
}) {
  const value = useMemo<I18nState>(
    () => ({ locale, t: dictionaries[locale] ?? en }),
    [locale],
  );
  return createElement(I18nContext.Provider, { value }, children);
}

// Returns the active dictionary so components can read strings as `t.feature.key`.
export function useT(): Dict {
  return useContext(I18nContext).t;
}
