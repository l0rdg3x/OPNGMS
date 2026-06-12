import { useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Card,
  Group,
  Loader,
  Modal,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Table,
  Text,
  Title,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import {
  downloadLogFleet,
  useLogFleet,
  useLogFleetDevices,
  useSilentTenantAlerts,
} from "../logs/logFleetHooks";

async function exportFleet(window: string, format: "csv" | "pdf") {
  try {
    await downloadLogFleet(window, format);
  } catch {
    notifications.show({ color: "red", message: "Export failed." });
  }
}

const WINDOWS = ["24h", "7d", "30d"];

// Device forwarding label -> badge color.
const FWD_COLOR: Record<string, string> = {
  enabled: "green",
  disabled: "gray",
  revoked: "red",
  none: "gray",
};

function isSilent(enabled: number, lastLogAt: string | null): boolean {
  if (enabled <= 0) return false;
  if (!lastLogAt) return true;
  return Date.now() - new Date(lastLogAt).getTime() > 60 * 60 * 1000; // > 1h
}

// Drill-down: the per-device list for one tenant (silent flag computed server-side).
function TenantDevicesModal({
  tenant,
  window,
  onClose,
}: {
  tenant: { id: string; name: string } | null;
  window: string;
  onClose: () => void;
}) {
  const q = useLogFleetDevices(tenant?.id ?? null, window);
  return (
    <Modal opened={!!tenant} onClose={onClose} size="xl" title={tenant ? `Devices — ${tenant.name}` : ""}>
      {q.isLoading && <Loader size="sm" />}
      {q.isError && <Alert color="red">Failed to load devices.</Alert>}
      {q.data && q.data.devices.length === 0 && <Text c="dimmed">No devices.</Text>}
      {q.data && q.data.devices.length > 0 && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Device</Table.Th><Table.Th>Forwarding</Table.Th>
              <Table.Th>Last log</Table.Th><Table.Th>Volume {q.data.window}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.devices.map((d) => (
              <Table.Tr key={d.device_id} data-testid={`fleet-device-${d.device_id}`}>
                <Table.Td>
                  <Group gap="xs">
                    {d.name}
                    {d.is_silent && (
                      <Badge color="red" variant="light" data-testid={`device-silent-${d.device_id}`}>
                        silent
                      </Badge>
                    )}
                  </Group>
                </Table.Td>
                <Table.Td>
                  <Badge color={FWD_COLOR[d.forwarding] ?? "gray"} variant="light">{d.forwarding}</Badge>
                </Table.Td>
                <Table.Td>{d.last_log_at ?? "—"}</Table.Td>
                <Table.Td>{d.volume ?? "—"}</Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Modal>
  );
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
  const [drill, setDrill] = useState<{ id: string; name: string } | null>(null);
  const fleet = useLogFleet(window);
  const silentAlerts = useSilentTenantAlerts();

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

  const alerts = silentAlerts.data ?? [];

  return (
    <Stack>
      {alerts.length > 0 && (
        <Alert color="red" title="Silent-tenant alerts" data-testid="silent-alert-banner">
          {alerts.map((a) => a.tenant_name).join(", ")} — enabled forwarding but no recent logs.
        </Alert>
      )}
      <Group justify="space-between">
        <Title order={3}>Log fleet</Title>
        <Group gap="sm">
          <Button variant="default" size="xs" onClick={() => exportFleet(window, "csv")}>
            Export CSV
          </Button>
          <Button variant="default" size="xs" onClick={() => exportFleet(window, "pdf")}>
            Export PDF
          </Button>
          {selector}
        </Group>
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
            <Table.Tr
              key={t.tenant_id}
              style={{ cursor: "pointer" }}
              data-testid={`fleet-row-${t.tenant_id}`}
              onClick={() => setDrill({ id: t.tenant_id, name: t.tenant_name })}
            >
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

      <TenantDevicesModal tenant={drill} window={window} onClose={() => setDrill(null)} />
    </Stack>
  );
}
