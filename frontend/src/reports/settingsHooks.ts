import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";

type ReportSettingsOut = components["schemas"]["ReportSettingsOut"];
type ReportSettingsIn = components["schemas"]["ReportSettingsIn"];
type ReportLanguageOut = components["schemas"]["ReportLanguageOut"];

const settingsKey = (tenantId: string | null) => ["report-settings", tenantId];
const languagesKey = (tenantId: string | null) => ["report-languages", tenantId];

export function useReportSettings() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: settingsKey(activeId),
    enabled: !!activeId,
    queryFn: async (): Promise<ReportSettingsOut> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/reports/settings",
        { params: { path: { tenant_id: activeId! } } },
      );
      if (error) throw new Error(en.errors.reportSettingsLoad);
      return data!;
    },
  });
}

export function useReportLanguages() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: languagesKey(activeId),
    enabled: !!activeId,
    queryFn: async (): Promise<ReportLanguageOut[]> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/reports/languages",
        { params: { path: { tenant_id: activeId! } } },
      );
      if (error) throw new Error(en.errors.reportSettingsLoad);
      return data!;
    },
  });
}

export function useUpdateReportSettings() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ReportSettingsIn): Promise<ReportSettingsOut> => {
      const { data, error } = await api.PUT(
        "/api/tenants/{tenant_id}/reports/settings",
        { params: { path: { tenant_id: activeId! } }, body },
      );
      if (error || !data) throw new Error(en.errors.reportSettingsAction);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: settingsKey(activeId) }),
  });
}

export function useUploadLogo() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<void> => {
      const url = `${import.meta.env.VITE_API_BASE ?? ""}/api/tenants/${activeId}/reports/settings/logo`;
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(url, {
        method: "PUT",
        credentials: "include",
        headers: { "X-OPNGMS-CSRF": "1" },
        body: fd,
      });
      if (!res.ok) throw new Error(en.errors.reportSettingsAction);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: settingsKey(activeId) }),
  });
}

export function useDeleteLogo() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      const { error } = await api.DELETE(
        "/api/tenants/{tenant_id}/reports/settings/logo",
        { params: { path: { tenant_id: activeId! } } },
      );
      if (error) throw new Error(en.errors.reportSettingsAction);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: settingsKey(activeId) }),
  });
}
