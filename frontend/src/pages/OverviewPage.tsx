import { Alert, Badge, Loader, SimpleGrid, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useT } from "../i18n";
import { useAlerts, useTenantHealth } from "../monitoring/hooks";
import { HealthSummaryCards, type FleetHealth } from "../monitoring/HealthSummaryCards";
import { AttackerCountriesCard } from "../overview/AttackerCountriesCard";
import { PerimeterCard } from "../perimeter/PerimeterCard";

export function OverviewPage() {
  const t = useT();
  const health = useTenantHealth();
  const alerts = useAlerts(true);

  return (
    <Stack>
      <Title order={3}>{t.overview.title}</Title>
      {health.isLoading && <Loader />}
      {health.error && <Alert color="red">{t.overview.healthLoadError}</Alert>}
      {health.data && <HealthSummaryCards health={health.data as FleetHealth} />}

      <Title order={4} mt="md">{t.overview.attackerCountries.title}</Title>
      <AttackerCountriesCard />

      <Title order={4} mt="md">{t.perimeter.title}</Title>
      <SimpleGrid cols={{ base: 1, md: 2 }}>
        <PerimeterCard kind="login_failed" />
        <PerimeterCard kind="firewall_block" />
      </SimpleGrid>

      <Title order={4} mt="md">{t.overview.activeAlerts}</Title>
      {alerts.isLoading && <Loader />}
      {alerts.error && <Alert color="red">{t.overview.alertsLoadError}</Alert>}
      {alerts.data && alerts.data.length === 0 && (
        <Text c="dimmed">{t.overview.noActiveAlerts}</Text>
      )}
      {alerts.data && alerts.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.alerts.colType}</Table.Th>
              <Table.Th>{t.alerts.colLabel}</Table.Th>
              <Table.Th>{t.alerts.colSeverity}</Table.Th>
              <Table.Th>{t.alerts.colOpened}</Table.Th>
              <Table.Th>{t.alerts.colDevice}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {alerts.data.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>{a.type}</Table.Td>
                <Table.Td>{a.label || t.common.none}</Table.Td>
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
