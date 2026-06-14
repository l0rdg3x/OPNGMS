import { Card, Switch, Text } from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useT } from "../i18n";

type Toggles = { failed_logins: boolean; firewall_blocks: boolean };

/** Per-device toggles controlling whether this device contributes to the two perimeter report
 * sections. PATCHes the device (the frontend always sends both current values). */
export function ReportPerimeterToggles({
  tenantId,
  deviceId,
  value,
}: {
  tenantId: string;
  deviceId: string;
  value: Toggles;
}) {
  const t = useT();
  const tp = t.perimeter;
  const qc = useQueryClient();

  const update = useMutation({
    mutationFn: async (next: Toggles) => {
      const { data, error } = await api.PATCH("/api/tenants/{tenant_id}/devices/{device_id}", {
        params: { path: { tenant_id: tenantId, device_id: deviceId } },
        body: { report_perimeter: next },
      });
      if (error || !data) throw new Error("update failed");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["device", tenantId, deviceId] }),
    onError: () => notifications.show({ color: "red", message: tp.reportToggleError }),
  });

  const set = (patch: Partial<Toggles>) => update.mutate({ ...value, ...patch });

  return (
    <Card withBorder mt="md" data-testid="report-perimeter-toggles">
      <Text fw={600} size="sm">{tp.reportToggleTitle}</Text>
      <Switch
        mt="xs"
        label={tp.failedLogins}
        checked={value.failed_logins}
        onChange={(e) => set({ failed_logins: e.currentTarget.checked })}
        disabled={update.isPending}
        data-testid="report-toggle-failed_logins"
      />
      <Switch
        mt="xs"
        label={tp.firewallBlocks}
        checked={value.firewall_blocks}
        onChange={(e) => set({ firewall_blocks: e.currentTarget.checked })}
        disabled={update.isPending}
        data-testid="report-toggle-firewall_blocks"
      />
    </Card>
  );
}
