import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";
import { rangeToParams } from "./range";
import type { Range } from "./types";

export function useTenantHealth() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["health", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/health", {
        params: { path: { tenant_id: activeId! } },
      });
      if (error) throw new Error(en.errors.fleetHealthLoad);
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
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/alerts", {
        params: { path: { tenant_id: activeId! }, query: { active } },
      });
      if (error) throw new Error(en.errors.alertsLoad);
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
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/metrics",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId! },
            query: { metric, from, to, bucket },
          },
        },
      );
      if (error) throw new Error(en.errors.metricsLoad);
      return data;
    },
  });
}

/** Map of raw metric labels (interface/gateway/VPN identifiers) -> their assigned names, parsed
 *  from the device's latest config snapshot. Empty when no snapshot — charts fall back to the id. */
export function useMetricLabels(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["metric-labels", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<Record<string, string>> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/metric-labels",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (error) return {};
      return (data as Record<string, string>) ?? {};
    },
  });
}
