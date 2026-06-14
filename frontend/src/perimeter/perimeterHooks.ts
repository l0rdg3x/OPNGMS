import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";

export type PerimeterAttacker = components["schemas"]["PerimeterAttackerOut"];
export type PerimeterKind = "login_failed" | "firewall_block";

const DAY_MS = 24 * 60 * 60 * 1000;

function rangeFrom(windowDays: number): { frm: string; to: string } {
  const now = new Date();
  return { frm: new Date(now.getTime() - windowDays * DAY_MS).toISOString(), to: now.toISOString() };
}

/**
 * Top attacker IPs for the active tenant + perimeter `kind` over a window (default last 7 days).
 * Returns the ranked rows; country is `UNKNOWN`/`PRIVATE` sentinels or an ISO alpha-2 code.
 */
export function usePerimeterAttackers(
  kind: PerimeterKind,
  opts?: { windowDays?: number; limit?: number },
) {
  const { activeId } = useTenant();
  const windowDays = opts?.windowDays ?? 7;
  const limit = opts?.limit ?? 5;
  // Compute the range once per (mount, windowDays): a fresh `new Date()` per render would mint a new
  // queryKey and loop react-query.
  const range = useMemo(() => rangeFrom(windowDays), [windowDays]);
  return useQuery({
    queryKey: ["perimeter-attackers", activeId, kind, range.frm, range.to, limit],
    enabled: !!activeId,
    queryFn: async (): Promise<PerimeterAttacker[]> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/perimeter/attackers", {
        params: { path: { tenant_id: activeId! }, query: { kind, frm: range.frm, to: range.to, limit } },
      });
      if (error) throw new Error(en.errors.perimeterLoad);
      return data ?? [];
    },
  });
}
