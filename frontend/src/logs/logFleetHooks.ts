import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type LogFleetOut = components["schemas"]["LogFleetOut"];

export function useLogFleet() {
  return useQuery({
    queryKey: ["log-fleet"],
    queryFn: async (): Promise<LogFleetOut> => {
      const { data, error } = await api.GET("/api/admin/log-fleet");
      if (error || !data) throw new Error("log fleet failed");
      return data;
    },
  });
}
