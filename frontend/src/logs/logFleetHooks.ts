import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type LogFleetOut = components["schemas"]["LogFleetOut"];
export type LogFleetDevicesOut = components["schemas"]["LogFleetDevicesOut"];

export function useLogFleet(window: string) {
  return useQuery({
    queryKey: ["log-fleet", window],
    queryFn: async (): Promise<LogFleetOut> => {
      const { data, error } = await api.GET("/api/admin/log-fleet", {
        params: { query: { window } },
      });
      if (error || !data) throw new Error("log fleet failed");
      return data;
    },
  });
}

// Per-device drill-down for one tenant; enabled only while a tenant is selected.
export function useLogFleetDevices(tenantId: string | null, window: string) {
  return useQuery({
    queryKey: ["log-fleet-devices", tenantId, window],
    enabled: !!tenantId,
    queryFn: async (): Promise<LogFleetDevicesOut> => {
      const { data, error } = await api.GET(
        "/api/admin/log-fleet/tenants/{tenant_id}/devices",
        { params: { path: { tenant_id: tenantId! }, query: { window } } },
      );
      if (error || !data) throw new Error("log fleet devices failed");
      return data;
    },
  });
}
