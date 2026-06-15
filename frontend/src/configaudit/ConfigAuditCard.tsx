import { Alert, Badge, Card, Group, Loader, Stack, Text } from "@mantine/core";

import { useT } from "../i18n";
import { channelLabel, isDirectChannel, useConfigAuditSummary } from "./configAuditHooks";

/**
 * Compact Overview summary card: fleet config-change counts over the last 24h, ranked by change
 * CHANNEL (api/gui/system). Reads the existing `/events/top` aggregate; the direct (gui/system)
 * channels — the drift causes — are emphasized with a "Direct" badge.
 */
export function ConfigAuditCard() {
  const t = useT();
  const tr = t.configAudit;
  const { data, isLoading, error } = useConfigAuditSummary();

  return (
    <Card withBorder padding="lg" radius="md" data-testid="config-audit-card">
      <Group justify="space-between">
        <Text className="noc-eyebrow">{tr.title}</Text>
        <Text size="xs" c="dimmed">{tr.last24h}</Text>
      </Group>
      <Stack gap="xs" mt="md">
        {isLoading && <Loader size="sm" aria-label={tr.loading} />}
        {error && <Alert color="red">{tr.loadError}</Alert>}
        {data && data.length === 0 && <Text size="sm" c="dimmed">{tr.empty}</Text>}
        {data?.map((row) => (
          <Group key={row.value} justify="space-between" gap="sm" wrap="nowrap">
            <Group gap="xs" wrap="nowrap">
              <Text size="sm">{channelLabel(row.value, tr)}</Text>
              {isDirectChannel(row.value) && <Badge color="yellow">{tr.direct}</Badge>}
            </Group>
            <Text size="sm" c="dimmed">{row.count}</Text>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}
