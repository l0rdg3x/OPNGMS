import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type LogFleetOut = components["schemas"]["LogFleetOut"];
export type LogFleetDevicesOut = components["schemas"]["LogFleetDevicesOut"];
export type SilentTenantAlert = components["schemas"]["SilentTenantAlertOut"];

// Tenants currently in the silent-alert state (worker-maintained); polled for the dashboard banner.
export function useSilentTenantAlerts() {
  return useQuery({
    queryKey: ["silent-tenant-alerts"],
    queryFn: async (): Promise<SilentTenantAlert[]> => {
      const { data, error } = await api.GET("/api/admin/silent-tenant-alerts");
      if (error || !data) throw new Error("silent alerts failed");
      return data;
    },
    refetchInterval: 5 * 60 * 1000,
  });
}

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

// Download the fleet table as CSV or PDF for the current window (triggers a browser download).
export async function downloadLogFleet(window: string, format: "csv" | "pdf"): Promise<void> {
  const { data, error } = await api.GET("/api/admin/log-fleet/export", {
    params: { query: { window, format } },
    parseAs: "blob",
  });
  if (error || !data) throw new Error("export failed");
  const url = URL.createObjectURL(data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `log-fleet-${window}.${format}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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
