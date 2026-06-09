import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import { rangeToParams } from "./range";
import type { Range } from "./types";

export function useTenantHealth() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["health", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/health", {
        params: { path: { tenant_id: activeId! } },
      });
      return data;
    },
  });
}

export function useAlerts(active: boolean) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["alerts", activeId, active],
    enabled: !!activeId,
    queryFn: async () => {
      const { data } = await api.GET("/api/tenants/{tenant_id}/alerts", {
        params: { path: { tenant_id: activeId! }, query: { active } },
      });
      return data ?? [];
    },
  });
}

export function useDeviceMetrics(deviceId: string | undefined, metric: string, range: Range) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["metrics", activeId, deviceId, metric, range],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { from, to, bucket } = rangeToParams(range, new Date());
      const { data } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/metrics",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId! },
            query: { metric, from, to, bucket },
          },
        },
      );
      return data;
    },
  });
}
