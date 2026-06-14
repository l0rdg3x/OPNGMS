import type { Dict } from "../i18n";

// The 11 report section keys, in display order. The backend re-resolves any missing
// keys against its built-in defaults; the UI only sends the explicit map the user sets.
export const REPORT_SECTION_KEYS = [
  "summary",
  "health",
  "alerts_wan",
  "attacks",
  "attacker_countries",
  "failed_logins",
  "firewall_blocks",
  "web",
  "data",
  "status",
  "firmware_config",
  "applications",
  "web_filter",
] as const;

export type ReportSectionKey = (typeof REPORT_SECTION_KEYS)[number];

// Localized label for a section key.
export function sectionLabel(t: Dict, key: ReportSectionKey): string {
  return t.reports.sections[key];
}

// Seed switch state from a loaded `sections` map: a key absent from the map shows as
// "on" for display; a present key shows its stored value. The backend re-resolves anyway.
export function seedSectionState(
  sections: { [key: string]: boolean } | null | undefined,
): Record<ReportSectionKey, boolean> {
  const map = sections ?? {};
  return REPORT_SECTION_KEYS.reduce(
    (acc, key) => {
      acc[key] = key in map ? map[key] : true;
      return acc;
    },
    {} as Record<ReportSectionKey, boolean>,
  );
}

// Build the explicit `{ [key]: boolean }` map to send in the request body, in key order.
export function buildSectionsMap(
  state: Record<ReportSectionKey, boolean>,
): Record<string, boolean> {
  return REPORT_SECTION_KEYS.reduce(
    (acc, key) => {
      acc[key] = state[key];
      return acc;
    },
    {} as Record<string, boolean>,
  );
}
