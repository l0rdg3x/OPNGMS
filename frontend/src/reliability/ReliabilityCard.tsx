import { Alert, Card, Group, Loader, Stack, Text } from "@mantine/core";

import { useT } from "../i18n";
import { useReliabilitySummary } from "./reliabilityHooks";

/**
 * Compact Overview summary card: fleet service-event counts over the last 24h, ranked by event
 * name (reboot, service_crashed, filesystem_full, …). Reads the existing `/events/top` aggregate.
 */
export function ReliabilityCard() {
  const t = useT();
  const tr = t.reliability;
  const { data, isLoading, error } = useReliabilitySummary();

  return (
    <Card withBorder padding="lg" radius="md" data-testid="reliability-card">
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
            <Text size="sm">{row.value}</Text>
            <Text size="sm" c="dimmed">{row.count}</Text>
          </Group>
        ))}
      </Stack>
    </Card>
  );
}
