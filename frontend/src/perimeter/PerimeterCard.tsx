import { Alert, Anchor, Card, Group, Loader, Stack, Text } from "@mantine/core";
import { Link } from "react-router-dom";

import { useLocale, useT } from "../i18n";
import { countryLabel } from "./countryLabel";
import { usePerimeterAttackers, type PerimeterKind } from "./perimeterHooks";

/** Compact Overview summary card: the top attacker IPs for one perimeter kind. Links to /perimeter. */
export function PerimeterCard({ kind }: { kind: PerimeterKind }) {
  const t = useT();
  const { locale } = useLocale();
  const tp = t.perimeter;
  const tc = t.overview.attackerCountries;
  const { data, isLoading, error } = usePerimeterAttackers(kind, { limit: 5 });

  const title = kind === "login_failed" ? tp.failedLogins : tp.firewallBlocks;
  const labelCol = kind === "login_failed" ? tp.user : tp.port;

  return (
    <Card withBorder padding="lg" radius="md" data-testid={`perimeter-card-${kind}`}>
      <Group justify="space-between">
        <Text className="noc-eyebrow">{title}</Text>
        <Anchor component={Link} to="/perimeter" size="xs">{tp.viewAll}</Anchor>
      </Group>
      <Stack gap="xs" mt="md">
        {isLoading && <Loader size="sm" />}
        {error && <Alert color="red">{tp.loadError}</Alert>}
        {data && data.length === 0 && <Text size="sm" c="dimmed">{tp.empty}</Text>}
        {data?.map((row) => (
          <Group key={row.src_ip} justify="space-between" gap="sm" wrap="nowrap">
            <div>
              <Text size="sm">{row.src_ip}</Text>
              <Text size="xs" c="dimmed">
                {countryLabel(row.country, locale, tc.private, tc.unknown)}
                {row.label ? ` · ${labelCol} ${row.label}` : ""}
              </Text>
            </div>
            <Text size="sm" c="dimmed">{row.count}</Text>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}
