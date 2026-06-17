import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { components } from "../api/schema";

export type TrustedDevice = components["schemas"]["TrustedDeviceOut"];

export function useTrustedDevices(enabled: boolean) {
  return useQuery({
    queryKey: ["trusted-devices"],
    enabled,
    queryFn: async (): Promise<TrustedDevice[]> => {
      const { data, error } = await api.GET("/api/me/trusted-devices");
      if (error || !data) throw new Error("Failed to load trusted devices");
      return data;
    },
  });
}

export function useRevokeTrustedDevice() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/me/trusted-devices/{device_id}", {
        params: { path: { device_id: id } },
      });
      if (error) throw new Error("Could not revoke the device");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trusted-devices"] }),
  });
}

export function useRevokeAllTrustedDevices() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      const { error } = await api.DELETE("/api/me/trusted-devices");
      if (error) throw new Error("Could not revoke trusted devices");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["trusted-devices"] }),
  });
}
