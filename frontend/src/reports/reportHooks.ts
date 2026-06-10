import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";

export type GeneratedReportOut = components["schemas"]["GeneratedReportOut"];

const generatedReportsKey = (tenantId: string | null) => ["generated-reports", tenantId];

function downloadBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function useGeneratedReports() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: generatedReportsKey(activeId),
    enabled: !!activeId,
    queryFn: async (): Promise<GeneratedReportOut[]> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/reports", {
        params: { path: { tenant_id: activeId! } },
      });
      if (error) throw new Error(en.errors.reportsLoad);
      return data ?? [];
    },
  });
}

export function useGenerateReport() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ from, to }: { from: string; to: string }) => {
      const base = import.meta.env.VITE_API_BASE ?? "";
      const res = await fetch(`${base}/api/tenants/${activeId}/reports`, {
        method: "POST",
        credentials: "include",
        headers: {
          "X-OPNGMS-CSRF": "1",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ from, to }),
      });
      if (!res.ok) throw new Error(en.errors.reportGenerate);
      const blob = await res.blob();
      downloadBlob(blob, "report.pdf");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: generatedReportsKey(activeId) }),
  });
}

export function useDownloadReport() {
  const { activeId } = useTenant();
  return useMutation({
    mutationFn: async (reportId: string) => {
      const base = import.meta.env.VITE_API_BASE ?? "";
      const res = await fetch(
        `${base}/api/tenants/${activeId}/reports/${reportId}/download`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(en.errors.reportsLoad);
      const blob = await res.blob();
      downloadBlob(blob, `report-${reportId}.pdf`);
    },
  });
}
