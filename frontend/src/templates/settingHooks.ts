import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

export type SettingField = {
  path: string;
  label: string;
  control: "select" | "multiselect" | "switch" | "text";
  options?: { value: string; label: string }[];
  value: string | string[];
};

export function useSettingEndpoints() {
  return useQuery({
    queryKey: ["setting-endpoints"],
    queryFn: async (): Promise<{ key: string; label: string }[]> => {
      const { data, error } = await api.GET("/api/opnsense/setting-endpoints");
      if (error || !data) throw new Error("setting endpoints load failed");
      return data as { key: string; label: string }[];
    },
  });
}

export function useTenantDevices() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["devices", activeId],
    enabled: !!activeId,
    queryFn: async () => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/devices", {
        params: { path: { tenant_id: activeId! } },
      });
      if (error || !data) throw new Error("devices load failed");
      return data;
    },
  });
}

export function useIntrospectSetting(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (endpointKey: string): Promise<{ fields: SettingField[]; label: string }> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/opnsense/settings/{endpoint_key}",
        {
          params: {
            path: {
              tenant_id: activeId!,
              device_id: deviceId,
              endpoint_key: endpointKey,
            },
          },
        },
      );
      if (error || !data) throw new Error(t.templates.setting.loadFailed);
      return data as { fields: SettingField[]; label: string };
    },
  });
}
