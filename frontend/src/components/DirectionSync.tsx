import { useDirection } from "@mantine/core";
import { useEffect } from "react";
import { useLocale } from "../i18n";
import { isRtl } from "../i18n/locale";

/**
 * Keeps the document/layout direction in sync with the active UI locale: RTL for Arabic,
 * LTR otherwise. Updates Mantine's direction context (so JS-aware components mirror) and the
 * `<html dir>` attribute (which drives Mantine's CSS mirroring). Render once, inside both the
 * I18nProvider and the DirectionProvider.
 */
export function DirectionSync() {
  const { locale } = useLocale();
  const { setDirection } = useDirection();
  const dir = isRtl(locale) ? "rtl" : "ltr";

  useEffect(() => {
    setDirection(dir);
    document.documentElement.setAttribute("dir", dir);
  }, [dir, setDirection]);

  return null;
}
