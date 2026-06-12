import { useState } from "react";
import {
  Alert,
  Badge,
  Card,
  Group,
  Loader,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";

import { useLogFleet } from "../logs/logFleetHooks";

const WINDOWS = ["24h", "7d", "30d"];

function isSilent(enabled: number, lastLogAt: string | null): boolean {
  if (enabled <= 0) return false;
  if (!lastLogAt) return true;
  return Date.now() - new Date(lastLogAt).getTime() > 60 * 60 * 1000; // > 1h
}

function StatCard({ label, value, testid }: { label: string; value: number; testid?: string }) {
  return (
    <Card withBorder padding="md" radius="md">
      <Text size="xs" c="dimmed">{label}</Text>
      <Text size="xl" fw={700} data-testid={testid}>{value}</Text>
    </Card>
  );
}

export function LogFleetPage() {
  const [window, setWindow] = useState("24h");
  const fleet = useLogFleet(window);

  const selector = (
    <SegmentedControl
      value={window}
      onChange={setWindow}
      data={WINDOWS}
      data-testid="fleet-window-selector"
    />
  );

  if (fleet.isLoading) {
    return (
      <Stack>
        <Group justify="space-between">
          <Title order={3}>Log fleet</Title>
          {selector}
        </Group>
        <Loader />
      </Stack>
    );
  }
  if (fleet.isError || !fleet.data) {
    return (
      <Stack>
        <Group justify="space-between">
          <Title order={3}>Log fleet</Title>
          {selector}
        </Group>
        <Alert color="red">Failed to load the log fleet.</Alert>
      </Stack>
    );
  }
  const { tenants, totals } = fleet.data;
  // Label the volume column with the window actually applied by the API.
  const windowLabel = fleet.data.window;

  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Log fleet</Title>
        {selector}
      </Group>
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard label="Tenants forwarding" value={totals.tenants_with_forwarding} />
        <StatCard label="Enabled devices" value={totals.enabled_devices} />
        <StatCard label={`Volume (${windowLabel})`} value={totals.volume} />
        <StatCard label="Silent tenants" value={totals.silent_tenants} testid="fleet-silent-count" />
      </SimpleGrid>

      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Tenant</Table.Th><Table.Th>Forwarding</Table.Th><Table.Th>Revoked</Table.Th>
            <Table.Th>Last log</Table.Th><Table.Th>Volume {windowLabel}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {tenants.map((t) => (
            <Table.Tr key={t.tenant_id}>
              <Table.Td>
                <Group gap="xs">
                  {t.tenant_name}
                  {isSilent(t.enabled, t.last_log_at ?? null) && (
                    <Badge color="red" variant="light" data-testid={`fleet-silent-${t.tenant_id}`}>silent</Badge>
                  )}
                </Group>
              </Table.Td>
              <Table.Td>{t.enabled} / {t.total_devices}</Table.Td>
              <Table.Td>{t.revoked}</Table.Td>
              <Table.Td>{t.last_log_at ?? "—"}</Table.Td>
              <Table.Td>{t.volume ?? "—"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}
