import { useState } from "react";
import { Badge, Button, Card, Group, Table, Text, Title } from "@mantine/core";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import { useConfigChanges } from "./changeHooks";

// Pipeline status -> Mantine badge color.
const STATUS_COLOR: Record<string, string> = {
  draft: "gray",
  scheduled: "blue",
  applying: "yellow",
  applied: "green",
  conflict: "orange",
  failed: "red",
  cancelled: "gray",
};

export function ChangesPanel({ deviceId }: { deviceId: string }) {
  const t = useT();
  const { activeId, tenants } = useTenant();
  const role = tenants.find((x) => x.id === activeId)?.role ?? null;
  const canEdit = role === "tenant_admin" || role === "operator";
  const q = useConfigChanges(deviceId);
  // Modal/per-row actions are wired in later tasks; the state is ready here.
  const [, setProposeOpen] = useState(false);

  return (
    <Card withBorder>
      <Group justify="space-between" mb="xs">
        <Title order={5}>{t.config.changes.title}</Title>
        {canEdit && (
          <Button size="xs" onClick={() => setProposeOpen(true)}>
            {t.config.changes.propose}
          </Button>
        )}
      </Group>
      {q.data && q.data.length === 0 && (
        <Text c="dimmed">{t.config.changes.none}</Text>
      )}
      {q.data && q.data.length > 0 && (
        <Table>
          <Table.Thead>
            <Table.Tr>
              <Table.Th>{t.config.changes.colKind}</Table.Th>
              <Table.Th>{t.config.changes.colOperation}</Table.Th>
              <Table.Th>{t.config.changes.colTarget}</Table.Th>
              <Table.Th>{t.config.changes.colStatus}</Table.Th>
            </Table.Tr>
          </Table.Thead>
          <Table.Tbody>
            {q.data.map((c) => (
              <Table.Tr key={c.id}>
                <Table.Td>{c.kind}</Table.Td>
                <Table.Td>{c.operation}</Table.Td>
                <Table.Td>{c.target}</Table.Td>
                <Table.Td>
                  <Badge color={STATUS_COLOR[c.status] ?? "gray"}>
                    {c.status}
                  </Badge>
                </Table.Td>
              </Table.Tr>
            ))}
          </Table.Tbody>
        </Table>
      )}
      {/* ProposeAliasModal mounted in Task 3/4; proposeOpen state ready */}
    </Card>
  );
}
