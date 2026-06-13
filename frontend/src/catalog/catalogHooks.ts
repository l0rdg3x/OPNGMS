// frontend/src/catalog/catalogHooks.ts
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { CatalogChangeBody, CatalogModel, CatalogModelLive } from "./catalogTypes";

export function useDeviceCatalog(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useQuery({
    queryKey: ["device-catalog", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<{ resolved_version: string; models: Record<string, CatalogModel> }> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/catalog",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } } },
      );
      if (error || !data) throw new Error(t.catalog.loadFailed);
      return data as { resolved_version: string; models: Record<string, CatalogModel> };
    },
  });
}

export function useCatalogModel(deviceId: string, modelId: string | null) {
  const { activeId } = useTenant();
  const t = useT();
  return useQuery({
    queryKey: ["catalog-model", activeId, deviceId, modelId],
    enabled: !!activeId && !!deviceId && !!modelId,
    queryFn: async (): Promise<CatalogModelLive> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/catalog/models/{model_id}",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, model_id: modelId! } } },
      );
      if (error || !data) throw new Error(t.catalog.loadFailed);
      return data as CatalogModelLive;
    },
  });
}

export function useProposeCatalogChange(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (body: CatalogChangeBody) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/catalog/changes",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body },
      );
      if (error || !data) throw new Error(t.catalog.proposeFailed);
      return data;
    },
  });
}
