import { Card, Group, SimpleGrid, Text, Title } from "@mantine/core";
import { useT } from "../i18n";

export interface FleetHealth {
  total_devices: number;
  by_status: Record<string, number>;
  active_alerts: number;
}

export function HealthSummaryCards({ health }: { health: FleetHealth }) {
  const t = useT();
  return (
    <SimpleGrid cols={{ base: 1, sm: 3 }}>
      <Card withBorder>
        <Text size="sm" c="dimmed">{t.health.totalDevices}</Text>
        <Title order={2}>{health.total_devices}</Title>
        <Group gap="xs" mt="xs">
          {Object.entries(health.by_status).map(([status, count]) => (
            <Text key={status} size="sm">
              {status}: <b>{count}</b>
            </Text>
          ))}
        </Group>
      </Card>
      <Card withBorder>
        <Text size="sm" c="dimmed">{t.health.activeAlerts}</Text>
        <Title order={2}>{health.active_alerts}</Title>
      </Card>
    </SimpleGrid>
  );
}
