import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type Profile = components["schemas"]["ProfileOut"];
export type ProfileIn = components["schemas"]["ProfileIn"];
export type ProfileUpdateIn = components["schemas"]["ProfileUpdateIn"];
export type TemplatePreview = components["schemas"]["TemplatePreviewOut"];

export function useProfiles() {
  return useQuery({
    queryKey: ["profiles"],
    queryFn: async (): Promise<Profile[]> => {
      const { data, error } = await api.GET("/api/profiles");
      if (error || !data) throw new Error("profiles load failed");
      return data;
    },
  });
}

export function useCreateProfile() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (body: ProfileIn): Promise<Profile> => {
      const { data, error } = await api.POST("/api/profiles", { body });
      if (error || !data) throw new Error(t.templates.profiles.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });
}

export function useUpdateProfile() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: ProfileUpdateIn }): Promise<Profile> => {
      const { data, error } = await api.PUT("/api/profiles/{profile_id}", {
        params: { path: { profile_id: id } },
        body,
      });
      if (error || !data) throw new Error(t.templates.profiles.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });
}

export function useDeleteProfile() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/profiles/{profile_id}", {
        params: { path: { profile_id: id } },
      });
      if (error) throw new Error(t.templates.profiles.saveFailed);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["profiles"] }),
  });
}

// Apply-time bindings threaded into preview/apply (e.g. { interface: "wan" } for firewall_rule
// members; empty = floating / no-op for kinds without a bind hook).
type Bindings = Record<string, unknown>;

export function usePreviewProfile(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async ({
      profileId,
      bindings,
    }: {
      profileId: string;
      bindings: Bindings;
    }): Promise<TemplatePreview[]> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/preview",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId, profile_id: profileId },
          },
          body: { bindings },
        },
      );
      if (error || !data) throw new Error(t.templates.profiles.apply.failed);
      return data;
    },
  });
}

export function useApplyProfile(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({
      profileId,
      scheduled_at,
      bindings,
    }: {
      profileId: string;
      scheduled_at: string | null;
      bindings: Bindings;
    }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/profiles/{profile_id}/apply",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId, profile_id: profileId },
          },
          body: { scheduled_at, bindings },
        },
      );
      if (error || !data) throw new Error(t.templates.profiles.apply.failed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-changes", activeId, deviceId] }),
  });
}
