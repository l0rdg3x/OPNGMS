import { Alert, Badge, Loader, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useAlerts, useTenantHealth } from "../monitoring/hooks";
import { HealthSummaryCards, type FleetHealth } from "../monitoring/HealthSummaryCards";

export function OverviewPage() {
  const health = useTenantHealth();
  const alerts = useAlerts(true);

  return (
    <Stack>
      <Title order={3}>Overview</Title>
      {health.isLoading && <Loader />}
      {health.error && <Alert color="red">Errore nel caricamento della salute flotta</Alert>}
      {health.data && <HealthSummaryCards health={health.data as FleetHealth} />}

      <Title order={4} mt="md">Alert attivi</Title>
      {alerts.isLoading && <Loader />}
      {alerts.data && alerts.data.length === 0 && (
        <Text c="dimmed">Nessun alert attivo</Text>
      )}
      {alerts.data && alerts.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Tipo</Table.Th>
              <Table.Th>Etichetta</Table.Th>
              <Table.Th>Severità</Table.Th>
              <Table.Th>Aperto</Table.Th>
              <Table.Th>Device</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {alerts.data.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>{a.type}</Table.Td>
                <Table.Td>{a.label || "—"}</Table.Td>
                <Table.Td><Badge color={a.severity === "critical" ? "red" : "yellow"}>{a.severity}</Badge></Table.Td>
                <Table.Td>{new Date(a.opened_at).toLocaleString()}</Table.Td>
                <Table.Td><Link to={`/devices/${a.device_id}`}>{a.device_id.slice(0, 8)}</Link></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
