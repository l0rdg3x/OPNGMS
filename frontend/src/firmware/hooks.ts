import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type FirmwareAction = components["schemas"]["FirmwareActionOut"];
export type FirmwareCheck = components["schemas"]["FirmwareCheckOut"];
export type FirmwareActionIn = components["schemas"]["FirmwareActionIn"];

const TERMINAL = new Set(["done", "failed"]);

/** Poll the actions list while any action is still scheduled/running. */
export function useFirmwareActions(deviceId: string) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["firmware-actions", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    refetchInterval: (query) => {
      const rows = (query.state.data as FirmwareAction[] | undefined) ?? [];
      return rows.some((r) => !TERMINAL.has(r.status)) ? 3000 : false;
    },
    queryFn: async (): Promise<FirmwareAction[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/firmware/actions",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error("firmware actions load failed");
      return data;
    },
  });
}

/** "Check for updates" — POST returns the current update picture. */
export function useFirmwareCheck(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (): Promise<FirmwareCheck> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/firmware/check",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.firmware.checkFailed);
      return data;
    },
  });
}

/** Create a firmware/plugin action (now if scheduled_at is null, else deferred). */
export function useCreateFirmwareAction(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (body: FirmwareActionIn): Promise<FirmwareAction> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/firmware/action",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body },
      );
      if (error || !data) throw new Error(t.firmware.actionFailed);
      return data;
    },
    onSuccess: () =>
      qc.invalidateQueries({ queryKey: ["firmware-actions", activeId, deviceId] }),
  });
}
