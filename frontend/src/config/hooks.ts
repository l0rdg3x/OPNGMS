import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";
import type { ConfigNode } from "./types";

export function useConfigModel(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["config-model", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<ConfigNode | null> => {
      const { data, error, response } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/model",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (response.status === 404) return null; // no snapshot yet -> empty state
      if (error) throw new Error(en.errors.configModelLoad);
      return data as unknown as ConfigNode;
    },
  });
}

export function useConfigCapabilities(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["config-capabilities", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async () => {
      const { data, error, response } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/capabilities",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (response.status === 404) return null;
      if (error) throw new Error(en.errors.configCapabilitiesLoad);
      return data ?? null;
    },
  });
}
