import { Badge, Card, Stack, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { DeviceActions } from "../components/DeviceActions";
import { DeviceHealthSection } from "../monitoring/DeviceHealthSection";
import { useTenant } from "../tenant/useTenant";

export function DeviceDetailPage() {
  const { deviceId } = useParams();
  const { activeId } = useTenant();
  const { data: device } = useQuery({
    queryKey: ["device", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/devices/{device_id}", {
        params: { path: { tenant_id: activeId!, device_id: deviceId! } },
      });
      return data;
    },
  });
  if (!device) return null;
  return (
    <Stack>
      <Title order={3}>{device.name}</Title>
      <Card withBorder>
        <Text>URL: {device.base_url}</Text>
        <Text component="div">Stato: <Badge>{device.status}</Badge></Text>
        <Text>Firmware: {device.firmware_version ?? "—"}</Text>
      </Card>
      {deviceId && <DeviceHealthSection deviceId={deviceId} />}
      {activeId && deviceId && <DeviceActions tenantId={activeId} deviceId={deviceId} />}
    </Stack>
  );
}
