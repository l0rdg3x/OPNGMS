import { Alert, Card, Group, Loader, Progress, Stack, Text } from "@mantine/core";
import { useLocale, useT } from "../i18n";
import { useAttackerCountries } from "./attackerCountriesHooks";

/**
 * Resolve an attacker-country code to a viewer-localized display label.
 * Sentinels `PRIVATE` / `UNKNOWN` map to dedicated i18n strings; real ISO alpha-2
 * codes go through `Intl.DisplayNames`, falling back to the raw code on any error.
 */
function countryLabel(
  code: string,
  locale: string,
  privateLabel: string,
  unknownLabel: string,
): string {
  if (code === "PRIVATE") return privateLabel;
  if (code === "UNKNOWN") return unknownLabel;
  try {
    const names = new Intl.DisplayNames([locale], { type: "region" });
    return names.of(code) ?? code;
  } catch {
    return code;
  }
}

export function AttackerCountriesCard() {
  const t = useT();
  const { locale } = useLocale();
  const { data, isLoading, error } = useAttackerCountries();
  const tc = t.overview.attackerCountries;

  return (
    <Card withBorder padding="lg" radius="md">
      <Text className="noc-eyebrow">{tc.title}</Text>
      <Stack gap="sm" mt="md">
        {isLoading && <Loader size="sm" />}
        {error && <Alert color="red">{tc.title}</Alert>}
        {data && data.length === 0 && <Text c="dimmed">{tc.empty}</Text>}
        {data &&
          data.length > 0 &&
          data.map((row) => (
            <Stack key={row.code} gap={4}>
              <Group justify="space-between" gap="sm" wrap="nowrap">
                <Text size="sm">
                  {countryLabel(row.code, locale, tc.private, tc.unknown)}
                </Text>
                <Text size="sm" c="dimmed">
                  {row.count} · {Math.round(row.pct)}%
                </Text>
              </Group>
              <Progress value={row.pct} size="sm" radius="sm" color="red" />
            </Stack>
          ))}
        <Text size="xs" c="dimmed" mt="xs">
          {tc.attribution}
        </Text>
      </Stack>
    </Card>
  );
}
