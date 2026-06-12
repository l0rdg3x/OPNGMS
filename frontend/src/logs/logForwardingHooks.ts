import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type LogForwardingOut = components["schemas"]["LogForwardingOut"];

export function useLogForwardingStatus(deviceId: string) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["log-forwarding", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<LogForwardingOut> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } });
      if (error || !data) throw new Error("status failed");
      return data;
    },
  });
}

export function useEnableLogForwarding(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<LogForwardingOut> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding/enable",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } });
      if (error || !data) throw new Error("enable failed");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["log-forwarding", activeId, deviceId] }),
  });
}

export function useDisableLogForwarding(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<LogForwardingOut> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/log-forwarding/disable",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } });
      if (error || !data) throw new Error("disable failed");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["log-forwarding", activeId, deviceId] }),
  });
}
