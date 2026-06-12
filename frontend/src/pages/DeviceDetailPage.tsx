import { Badge, Card, Loader, Stack, Tabs, Text, Title } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router-dom";
import { api } from "../api/client";
import { DeviceActions } from "../components/DeviceActions";
import { ConfigTab } from "../config/ConfigTab";
import { FirmwareActions } from "../firmware/FirmwareActions";
import { LogForwardingCard } from "../components/LogForwardingCard";
import { useT } from "../i18n";
import { DeviceHealthSection } from "../monitoring/DeviceHealthSection";
import { ApplyProfileSection } from "../profiles/ApplyProfileSection";
import { ApplyTemplateTab } from "../templates/ApplyTemplateTab";
import { useTenant } from "../tenant/useTenant";

export function DeviceDetailPage() {
  const t = useT();
  const { deviceId } = useParams();
  const { activeId } = useTenant();
  const { data: device, isLoading, error } = useQuery({
    queryKey: ["device", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/devices/{device_id}", {
        params: { path: { tenant_id: activeId!, device_id: deviceId! } },
      });
      return data;
    },
  });
  if (isLoading) return <Loader data-testid="device-detail-loader" />;
  if (error) return <Text c="red" data-testid="device-detail-error">{t.deviceDetail.loadError}</Text>;
  if (!device) return null;
  return (
    <Stack>
      <Title order={3}>{device.name}</Title>
      <Tabs defaultValue="info">
        <Tabs.List>
          <Tabs.Tab value="info">{t.config.tabInfo}</Tabs.Tab>
          <Tabs.Tab value="health">{t.config.tabHealth}</Tabs.Tab>
          <Tabs.Tab value="config">{t.config.tabConfig}</Tabs.Tab>
          <Tabs.Tab value="firmware">{t.firmware.tab}</Tabs.Tab>
          <Tabs.Tab value="forwarding">{t.logForwarding.tab}</Tabs.Tab>
          <Tabs.Tab value="templates">{t.templates.tab}</Tabs.Tab>
        </Tabs.List>
        <Tabs.Panel value="info" pt="md">
          <Card withBorder>
            <Text>{t.deviceDetail.url}: {device.base_url}</Text>
            <Text component="div">{t.deviceDetail.status}: <Badge>{device.status}</Badge></Text>
            <Text>{t.deviceDetail.firmware}: {device.firmware_version ?? t.common.none}</Text>
          </Card>
          {activeId && deviceId && (
            <DeviceActions tenantId={activeId} deviceId={deviceId} baseUrl={device.base_url} />
          )}
        </Tabs.Panel>
        <Tabs.Panel value="health" pt="md">
          {deviceId && <DeviceHealthSection deviceId={deviceId} />}
        </Tabs.Panel>
        <Tabs.Panel value="config" pt="md">
          {deviceId && <ConfigTab deviceId={deviceId} />}
        </Tabs.Panel>
        <Tabs.Panel value="firmware" pt="md">
          {deviceId && <FirmwareActions deviceId={deviceId} />}
        </Tabs.Panel>
        <Tabs.Panel value="forwarding" pt="md">
          {deviceId && <LogForwardingCard deviceId={deviceId} />}
        </Tabs.Panel>
        <Tabs.Panel value="templates" pt="md">
          {deviceId && (
            <Stack>
              <ApplyTemplateTab deviceId={deviceId} />
              <ApplyProfileSection deviceId={deviceId} />
            </Stack>
          )}
        </Tabs.Panel>
      </Tabs>
    </Stack>
  );
}
