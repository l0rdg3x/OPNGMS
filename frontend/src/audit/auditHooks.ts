import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type AuditEntryOut = components["schemas"]["AuditEntryOut"];
export type AuditListOut = components["schemas"]["AuditListOut"];

// All audit filters are optional; empty strings are normalised to undefined so they're omitted
// from the query string (the backend treats an absent param as "no filter").
export interface AuditFilters {
  actor_user_id?: string;
  tenant_id?: string;
  action?: string;
  frm?: string;
  to?: string;
  limit?: number;
  offset?: number;
}

// Drop empty/blank values so the query key + request only carry active filters.
function clean(filters: AuditFilters): AuditFilters {
  const out: AuditFilters = {};
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null) continue;
    if (typeof v === "string" && v.trim() === "") continue;
    (out as Record<string, unknown>)[k] = v;
  }
  return out;
}

export function useAuditQuery(filters: AuditFilters) {
  const query = clean(filters);
  return useQuery({
    queryKey: ["audit", query],
    queryFn: async (): Promise<AuditListOut> => {
      const { data, error } = await api.GET("/api/admin/audit", {
        params: { query },
      });
      if (error || !data) throw new Error("audit query failed");
      return data;
    },
  });
}

// Download the filtered audit ledger as CSV (triggers a browser download). Pagination is ignored —
// the export streams every matching row.
export async function downloadAuditCsv(filters: AuditFilters): Promise<void> {
  // The export endpoint takes the filters but no pagination — it streams every matching row.
  const { actor_user_id, tenant_id, action, frm, to } = clean(filters);
  const { data, error } = await api.GET("/api/admin/audit/export.csv", {
    params: { query: { actor_user_id, tenant_id, action, frm, to } },
    parseAs: "blob",
  });
  if (error || !data) throw new Error("export failed");
  const url = URL.createObjectURL(data as Blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "audit.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
