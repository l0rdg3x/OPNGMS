import { Badge, Button, Group, Loader, Table, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import { DeviceCreateModal } from "../components/DeviceCreateModal";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

const STATUS_COLOR: Record<string, string> = {
  reachable: "green",
  unverified: "yellow",
  unreachable: "red",
};

export function DevicesPage() {
  const t = useT();
  const { activeId } = useTenant();
  const navigate = useNavigate();
  const [createOpen, setCreateOpen] = useState(false);
  const { data: devices, isLoading, error } = useQuery({
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
        <Title order={3}>{t.devices.title}</Title>
        <Button onClick={() => setCreateOpen(true)} disabled={!activeId}>{t.devices.add}</Button>
      </Group>
      {isLoading && <Loader data-testid="devices-loader" />}
      {error && <Text c="red" data-testid="devices-error">{t.devices.loadError}</Text>}
      <Table highlightOnHover>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>{t.devices.colName}</Table.Th><Table.Th>{t.devices.colUrl}</Table.Th>
            <Table.Th>{t.devices.colStatus}</Table.Th><Table.Th>{t.devices.colFirmware}</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {(devices ?? []).map((d) => (
            <Table.Tr key={d.id} style={{ cursor: "pointer" }} onClick={() => navigate(`/devices/${d.id}`)}>
              <Table.Td>{d.name}</Table.Td>
              <Table.Td>{d.base_url}</Table.Td>
              <Table.Td><Badge color={STATUS_COLOR[d.status] ?? "gray"}>{d.status}</Badge></Table.Td>
              <Table.Td>{d.firmware_version ?? t.common.none}</Table.Td>
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
