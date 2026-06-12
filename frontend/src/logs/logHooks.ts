import { useMutation } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type LogSearchOut = components["schemas"]["LogSearchOut"];
export type LogSearchIn = components["schemas"]["LogSearchIn"];

export function useLogSearch() {
  const { activeId } = useTenant();
  return useMutation({
    mutationFn: async (body: LogSearchIn): Promise<LogSearchOut> => {
      const { data, error } = await api.POST("/api/tenants/{tenant_id}/logs/search",
        { params: { path: { tenant_id: activeId! } }, body });
      if (error || !data) throw new Error("Log search failed");
      return data;
    },
  });
}
