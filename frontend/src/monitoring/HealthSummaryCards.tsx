import { Badge, Card, Group, SimpleGrid, Stack, Text } from "@mantine/core";
import { useT } from "../i18n";

export interface FleetHealth {
  total_devices: number;
  by_status: Record<string, number>;
  active_alerts: number;
}

// Map a device status to a semantic accent for its chip.
function statusColor(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("reach") && !s.includes("un")) return "signal";
  if (s.includes("unreach") || s.includes("error") || s.includes("auth")) return "red";
  if (s.includes("unverified") || s.includes("pending")) return "amber";
  return "gray";
}

function Tile({ label, value, accent, children }: {
  label: string; value: number; accent?: string; children?: React.ReactNode;
}) {
  return (
    <Card withBorder padding="lg" radius="md">
      <Text className="noc-eyebrow">{label}</Text>
      <Text
        className="noc-metric"
        mt={6}
        style={{ fontSize: 44, color: accent ? `var(--mantine-color-${accent}-4)` : undefined }}
      >
        {value}
      </Text>
      {children && <Group gap={6} mt="md">{children}</Group>}
    </Card>
  );
}

export function HealthSummaryCards({ health }: { health: FleetHealth }) {
  const t = useT();
  const alertAccent = health.active_alerts > 0 ? "red" : "signal";
  return (
    <SimpleGrid cols={{ base: 1, sm: 3 }} spacing="lg">
      <Tile label={t.health.totalDevices} value={health.total_devices}>
        {Object.entries(health.by_status).map(([status, count]) => (
          <Badge key={status} size="sm" variant="light" color={statusColor(status)} radius="sm">
            {status} · {count}
          </Badge>
        ))}
      </Tile>

      <Tile label={t.health.activeAlerts} value={health.active_alerts} accent={alertAccent}>
        <Badge size="sm" variant="light" color={alertAccent} radius="sm">
          {health.active_alerts > 0 ? "attention" : "all clear"}
        </Badge>
      </Tile>

      <Card withBorder padding="lg" radius="md">
        <Text className="noc-eyebrow">Fleet</Text>
        <Stack gap={4} mt="sm">
          <Text size="sm" c="dimmed">
            {health.total_devices} device{health.total_devices === 1 ? "" : "s"} under management
          </Text>
          <Text size="sm" c="dimmed">{Object.keys(health.by_status).length} distinct states</Text>
        </Stack>
      </Card>
    </SimpleGrid>
  );
}
