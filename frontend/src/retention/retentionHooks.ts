import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";

type RetentionOut = components["schemas"]["RetentionOut"];
type RetentionPatch = components["schemas"]["RetentionPatch"];

const retentionKey = (tenantId: string | null) => ["retention", tenantId];

export function useRetention() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: retentionKey(activeId),
    enabled: !!activeId,
    queryFn: async (): Promise<RetentionOut> => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/retention",
        { params: { path: { tenant_id: activeId! } } },
      );
      if (error) throw new Error(en.errors.retentionLoad);
      return data!;
    },
  });
}

export function useUpdateRetention() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (values: RetentionPatch["values"]): Promise<RetentionOut> => {
      const { data, error } = await api.PUT(
        "/api/tenants/{tenant_id}/retention",
        { params: { path: { tenant_id: activeId! } }, body: { values } },
      );
      if (error || !data) throw new Error(en.errors.retentionSave);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: retentionKey(activeId) }),
  });
}
