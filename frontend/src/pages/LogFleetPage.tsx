import { Alert, Badge, Card, Group, Loader, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";

import { useLogFleet } from "../logs/logFleetHooks";

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
  const fleet = useLogFleet();
  if (fleet.isLoading) return <Loader />;
  if (fleet.isError || !fleet.data) return <Alert color="red">Failed to load the log fleet.</Alert>;
  const { tenants, totals } = fleet.data;

  return (
    <Stack>
      <Title order={3}>Log fleet</Title>
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard label="Tenants forwarding" value={totals.tenants_with_forwarding} />
        <StatCard label="Enabled devices" value={totals.enabled_devices} />
        <StatCard label="Volume (24h)" value={totals.volume_24h} />
        <StatCard label="Silent tenants" value={totals.silent_tenants} testid="fleet-silent-count" />
      </SimpleGrid>

      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Tenant</Table.Th><Table.Th>Forwarding</Table.Th><Table.Th>Revoked</Table.Th>
            <Table.Th>Last log</Table.Th><Table.Th>Volume 24h</Table.Th>
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
              <Table.Td>{t.volume_24h ?? "—"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
    </Stack>
  );
}
