import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";

export type PluginInfo = components["schemas"]["PluginInfoOut"];

/** The plugins the box last reported (installed + available), for the per-device Plugins tab. */
export function useDevicePlugins(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useQuery({
    queryKey: ["device-plugins", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<PluginInfo[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/plugins",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.plugins.loadFailed);
      return data;
    },
  });
}

export type PluginModel = components["schemas"]["PluginModelOut"];

/** Map of plugin package -> its editable config model id (plugins that have a config model). */
export function usePluginModels(deviceId: string) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["plugin-models", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<PluginModel[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/plugin-models",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) return [];   // configurability is optional enrichment — degrade quietly
      return data;
    },
  });
}
