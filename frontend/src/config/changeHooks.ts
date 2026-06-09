import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";
import type { ConfigChange } from "./changeTypes";

// Body shape for creating a change (aligned to the generated ConfigChangeIn:
// `operation` is the "add" | "set" | "delete" enum, payload a free-form dict).
type ConfigChangeIn = components["schemas"]["ConfigChangeIn"];

const listKey = (t: string | null, d: string | undefined) => ["config-changes", t, d];

export function useConfigChanges(deviceId: string | undefined) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: listKey(activeId, deviceId),
    enabled: !!activeId && !!deviceId,
    queryFn: async (): Promise<ConfigChange[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes",
        { params: { path: { tenant_id: activeId!, device_id: deviceId! } } },
      );
      if (error) throw new Error(en.errors.configChangesLoad);
      return data ?? [];
    },
  });
}

export function useCreateChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ConfigChangeIn) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes",
        { params: { path: { tenant_id: activeId!, device_id: deviceId } }, body },
      );
      if (error || !data) throw new Error(en.errors.configChangeAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey(activeId, deviceId) }),
  });
}

export function useScheduleChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, scheduled_at }: { id: string; scheduled_at: string | null }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/schedule",
        {
          params: { path: { tenant_id: activeId!, device_id: deviceId, change_id: id } },
          body: { scheduled_at },
        },
      );
      if (error || !data) throw new Error(en.errors.configChangeAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey(activeId, deviceId) }),
  });
}

export function useCancelChange(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/cancel",
        { params: { path: { tenant_id: activeId!, device_id: deviceId, change_id: id } } },
      );
      if (error || !data) throw new Error(en.errors.configChangeAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: listKey(activeId, deviceId) }),
  });
}

export function usePreviewChange(deviceId: string, changeId: string | null) {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: ["config-change-preview", activeId, deviceId, changeId],
    enabled: !!activeId && !!changeId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/devices/{device_id}/config/changes/{change_id}/preview",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId, change_id: changeId! },
          },
        },
      );
      // /preview returns a free-form dict (secret-safe) — typed loosely here.
      if (error) throw new Error(en.errors.configChangeAction);
      return data;
    },
  });
}
