import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";

const DAY_MS = 24 * 60 * 60 * 1000;

/** Default range: the last 7 days, as ISO-8601 strings. */
function defaultRange(): { frm: string; to: string } {
  const now = new Date();
  return {
    frm: new Date(now.getTime() - 7 * DAY_MS).toISOString(),
    to: now.toISOString(),
  };
}

/**
 * Top attacker countries for the active tenant over a time window (default: last 7 days).
 * Returns `[]` when the backend has no GeoIP database (graceful degrade → empty state).
 */
export function useAttackerCountries(opts?: { frm?: string; to?: string }) {
  const { activeId } = useTenant();
  // Compute the default range once per mount: calling `new Date()` on every render would
  // mint a fresh queryKey each time, which would put react-query into a refetch loop.
  const range = useMemo(() => defaultRange(), []);
  const frm = opts?.frm ?? range.frm;
  const to = opts?.to ?? range.to;
  return useQuery({
    queryKey: ["attacker-countries", activeId, frm, to],
    enabled: !!activeId,
    queryFn: async () => {
      const { data, error } = await api.GET(
        "/api/tenants/{tenant_id}/attacker-countries",
        {
          params: { path: { tenant_id: activeId! }, query: { frm, to } },
        },
      );
      if (error) throw new Error(en.errors.attackerCountriesLoad);
      return data ?? [];
    },
  });
}
