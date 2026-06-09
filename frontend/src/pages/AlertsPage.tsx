import { useState } from "react";
import { Badge, Group, Loader, SegmentedControl, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useT } from "../i18n";
import { useAlerts } from "../monitoring/hooks";

export function AlertsPage() {
  const t = useT();
  const [mode, setMode] = useState<"active" | "history">("active");
  const q = useAlerts(mode === "active");
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>{t.alerts.title}</Title>
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as "active" | "history")}
          data={[
            { label: t.alerts.active, value: "active" },
            { label: t.alerts.history, value: "history" },
          ]}
        />
      </Group>
      {q.isLoading && <Loader />}
      {q.error && <Text c="red">{t.alerts.loadError}</Text>}
      {q.data && q.data.length === 0 && <Text c="dimmed">{t.alerts.noAlerts}</Text>}
      {q.data && q.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.alerts.colType}</Table.Th>
              <Table.Th>{t.alerts.colLabel}</Table.Th>
              <Table.Th>{t.alerts.colSeverity}</Table.Th>
              <Table.Th>{t.alerts.colOpened}</Table.Th>
              <Table.Th>{t.alerts.colResolved}</Table.Th>
              <Table.Th>{t.alerts.colDevice}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>{a.type}</Table.Td>
                <Table.Td>{a.label || t.common.none}</Table.Td>
                <Table.Td>
                  <Badge color={a.severity === "critical" ? "red" : "yellow"}>{a.severity}</Badge>
                </Table.Td>
                <Table.Td>{new Date(a.opened_at).toLocaleString()}</Table.Td>
                <Table.Td>{a.resolved_at ? new Date(a.resolved_at).toLocaleString() : t.common.none}</Table.Td>
                <Table.Td><Link to={`/devices/${a.device_id}`}>{a.device_id.slice(0, 8)}</Link></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
