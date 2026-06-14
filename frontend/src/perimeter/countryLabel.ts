/**
 * Resolve an attacker-country code to a viewer-localized display label. Sentinels `PRIVATE` / `UNKNOWN`
 * map to the passed-in i18n strings; real ISO alpha-2 codes go through `Intl.DisplayNames`, falling back
 * to the raw code on any error. Mirrors the attacker-countries card helper.
 */
export function countryLabel(
  code: string,
  locale: string,
  privateLabel: string,
  unknownLabel: string,
): string {
  if (code === "PRIVATE") return privateLabel;
  if (code === "UNKNOWN") return unknownLabel;
  try {
    return new Intl.DisplayNames([locale], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}
