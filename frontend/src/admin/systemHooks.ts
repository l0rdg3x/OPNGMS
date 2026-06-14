import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type LivePushOut = components["schemas"]["LivePushOut"];
export type RuntimeSettingOut = components["schemas"]["RuntimeSettingOut"];
export type RuntimeSettingsOut = components["schemas"]["RuntimeSettingsOut"];
export type RetentionImpact = components["schemas"]["RetentionImpact"];

const livePushKey = () => ["live-push"] as const;

export function useLivePush() {
  return useQuery({
    queryKey: livePushKey(),
    queryFn: async (): Promise<LivePushOut> => {
      const { data, error } = await api.GET("/api/admin/live-push");
      if (error || !data) throw new Error("Failed to load live-push setting");
      return data;
    },
  });
}

export function useSetLivePush() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean): Promise<LivePushOut> => {
      const { data, error } = await api.PUT("/api/admin/live-push", { body: { enabled } });
      if (error || !data) throw new Error("Failed to update live-push setting");
      return data;
    },
    onSuccess: (data) => qc.setQueryData(livePushKey(), data),
  });
}

const runtimeSettingsKey = () => ["runtime-settings"] as const;

export function useRuntimeSettings() {
  return useQuery({
    queryKey: runtimeSettingsKey(),
    queryFn: async (): Promise<RuntimeSettingsOut> => {
      const { data, error } = await api.GET("/api/admin/settings");
      if (error || !data) throw new Error("Failed to load runtime settings");
      return data;
    },
  });
}

export function useUpdateRuntimeSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (values: Record<string, boolean | number>): Promise<RuntimeSettingsOut> => {
      const { data, error } = await api.PUT("/api/admin/settings", { body: { values } });
      if (error || !data) throw new Error("Failed to update runtime settings");
      return data;
    },
    onSuccess: (data) => qc.setQueryData(runtimeSettingsKey(), data),
  });
}
