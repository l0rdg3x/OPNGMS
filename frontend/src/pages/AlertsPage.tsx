import { useState } from "react";
import { Badge, Group, Loader, SegmentedControl, Stack, Table, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";
import { useAlerts } from "../monitoring/hooks";

export function AlertsPage() {
  const [mode, setMode] = useState<"active" | "history">("active");
  const q = useAlerts(mode === "active");
  return (
    <Stack>
      <Group justify="space-between">
        <Title order={3}>Alert</Title>
        <SegmentedControl
          value={mode}
          onChange={(v) => setMode(v as "active" | "history")}
          data={[
            { label: "Attivi", value: "active" },
            { label: "Storico", value: "history" },
          ]}
        />
      </Group>
      {q.isLoading && <Loader />}
      {q.error && <Text c="red">Errore nel caricamento degli alert</Text>}
      {q.data && q.data.length === 0 && <Text c="dimmed">Nessun alert</Text>}
      {q.data && q.data.length > 0 && (
        <Table striped withTableBorder>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>Tipo</Table.Th>
              <Table.Th>Etichetta</Table.Th>
              <Table.Th>Severità</Table.Th>
              <Table.Th>Aperto</Table.Th>
              <Table.Th>Risolto</Table.Th>
              <Table.Th>Device</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.map((a) => (
              <Table.Tr key={a.id}>
                <Table.Td>{a.type}</Table.Td>
                <Table.Td>{a.label || "—"}</Table.Td>
                <Table.Td>
                  <Badge color={a.severity === "critical" ? "red" : "yellow"}>{a.severity}</Badge>
                </Table.Td>
                <Table.Td>{new Date(a.opened_at).toLocaleString()}</Table.Td>
                <Table.Td>{a.resolved_at ? new Date(a.resolved_at).toLocaleString() : "—"}</Table.Td>
                <Table.Td><Link to={`/devices/${a.device_id}`}>{a.device_id.slice(0, 8)}</Link></Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
    </Stack>
  );
}
