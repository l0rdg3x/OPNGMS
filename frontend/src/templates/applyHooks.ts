import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type TemplatePreview = components["schemas"]["TemplatePreviewOut"];

/** Upsert this tenant's override for a library template (PUT .../override). */
export function useUpsertOverride(templateId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (body_patch: Record<string, unknown>) => {
      const { data, error } = await api.PUT(
        "/api/tenants/{tenant_id}/templates/{template_id}/override",
        {
          params: { path: { tenant_id: activeId!, template_id: templateId } },
          body: { body_patch },
        },
      );
      if (error || !data) throw new Error(t.templates.apply.failed);
      return data;
    },
  });
}

/** Preview the redacted effective body for a template on this device (POST, no body). */
export function usePreviewTemplate(deviceId: string) {
  const { activeId } = useTenant();
  const t = useT();
  return useMutation({
    mutationFn: async (templateId: string): Promise<TemplatePreview> => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/preview",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId, template_id: templateId },
          },
        },
      );
      if (error || !data) throw new Error(t.templates.apply.failed);
      return data;
    },
  });
}

/** Apply a template to the device now (scheduled_at null) or scheduled (POST .../apply). */
export function useApplyTemplate(deviceId: string) {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({
      templateId,
      scheduled_at,
    }: {
      templateId: string;
      scheduled_at: string | null;
    }) => {
      const { data, error } = await api.POST(
        "/api/tenants/{tenant_id}/devices/{device_id}/templates/{template_id}/apply",
        {
          params: {
            path: { tenant_id: activeId!, device_id: deviceId, template_id: templateId },
          },
          body: { scheduled_at },
        },
      );
      if (error || !data) throw new Error(t.templates.apply.failed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-changes", activeId, deviceId] }),
  });
}
