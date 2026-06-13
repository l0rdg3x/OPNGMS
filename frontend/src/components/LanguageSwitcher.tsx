import { Select } from "@mantine/core";
import { type Locale, useLocale, useT } from "../i18n";

/** Per-operator UI language picker (native names). Persists via the i18n provider. */
export function LanguageSwitcher({ w = 150, size }: { w?: number; size?: string }) {
  const t = useT();
  const { locale, setLocale, locales } = useLocale();
  return (
    <Select
      aria-label={t.common.language}
      data={locales}
      value={locale}
      onChange={(v) => v && setLocale(v as Locale)}
      allowDeselect={false}
      checkIconPosition="right"
      w={w}
      size={size}
      comboboxProps={{ withinPortal: true }}
    />
  );
}
