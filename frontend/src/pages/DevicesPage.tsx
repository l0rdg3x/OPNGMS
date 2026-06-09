import { Badge, Button, Group, Table, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { DeviceCreateModal } from "../components/DeviceCreateModal";
import { useTenant } from "../tenant/useTenant";

const STATUS_COLOR: Record<string, string> = {
  reachable: "green",
  unverified: "yellow",
  unreachable: "red",
};

export function DevicesPage() {
  const { activeId } = useTenant();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const { data: devices } = useQuery({
    queryKey: ["devices", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: activeId! } },
      });
      return data ?? [];
    },
  });

  return (
    <>
      <Group justify="space-between" mb="md">
        <Title order={3}>Device</Title>
        <Button onClick={() => setCreateOpen(true)} disabled={!activeId}>Aggiungi device</Button>
      </Group>
      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Nome</Table.Th><Table.Th>URL</Table.Th>
            <Table.Th>Stato</Table.Th><Table.Th>Firmware</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(devices ?? []).map((d) => (
            <Table.Tr key={d.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/devices/${d.id}`)}>
              <Table.Td>{d.name}</Table.Td>
              <Table.Td>{d.base_url}</Table.Td>
              <Table.Td><Badge color={STATUS_COLOR[d.status] ?? "gray"}>{d.status}</Badge></Table.Td>
              <Table.Td>{d.firmware_version ?? "—"}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      {activeId && (
        <DeviceCreateModal tenantId={activeId} opened={createOpen} onClose={() => setCreateOpen(false)} />
      )}
    </>
  );
}
