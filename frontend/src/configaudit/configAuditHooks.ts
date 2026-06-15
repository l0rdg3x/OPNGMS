import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import type { Dict } from "../i18n";
import { useTenant } from "../tenant/useTenant";

export type EventOut = components["schemas"]["EventOut"];
export type EventTopRow = components["schemas"]["EventTopRow"];

const DAY_MS = 24 * 60 * 60 * 1000;
const PAGE_SIZE = 50;

/** True for a DIRECT on-box change channel (a drift cause): console/script (`system`) or WebGUI (`gui`).
 * OPNGMS only ever writes via the `api` channel, so gui/system means a change made outside OPNGMS. */
export function isDirectChannel(action: string): boolean {
  return action === "gui" || action === "system";
}

/** Localized label for a change channel, falling back to the raw value for an unmapped channel. */
export function channelLabel(action: string, tr: Dict["configAudit"]): string {
  const labels: Record<string, string> = tr.channels;
  return labels[action] ?? action;
}

interface EventPage {
  items: EventOut[];
  next_cursor: string | null;
}

/**
 * Keyset-paginated timeline of one device's config-change audit events for the active tenant.
 * Pages on the opaque `next_cursor` returned by `GET /events`; `fetchNextPage` is enabled while a
 * cursor is present. Reuses the existing typed events API — no config-audit-specific endpoint.
 */
export function useConfigAuditEvents(deviceId: string) {
  const { activeId } = useTenant();
  return useInfiniteQuery({
    queryKey: ["config-audit-events", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<EventPage> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/events", {
        params: {
          path: { tenant_id: activeId! },
          query: {
            source: "config_audit",
            device_id: deviceId,
            limit: PAGE_SIZE,
            after: pageParam ?? undefined,
          },
        },
      });
      if (error) throw new Error(en.configAudit.loadError);
      return { items: data?.items ?? [], next_cursor: data?.next_cursor ?? null };
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/**
 * Fleet config-change counts over the last 24h, ranked by change CHANNEL (`action`): api/gui/system.
 * `action` IS in the events API's `/events/top` allow-list (unlike `category`), so we aggregate by it
 * to get the per-channel breakdown — emphasizing the gui/system (direct/drift) totals on the card.
 */
export function useConfigAuditSummary() {
  const { activeId } = useTenant();
  // Compute the range once per mount; a fresh `new Date()` each render would loop react-query.
  const frm = useMemo(() => new Date(new Date().getTime() - DAY_MS).toISOString(), []);
  return useQuery({
    queryKey: ["config-audit-summary", activeId, frm],
    enabled: !!activeId,
    queryFn: async (): Promise<EventTopRow[]> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/events/top", {
        params: {
          path: { tenant_id: activeId! },
          query: { source: "config_audit", field: "action", from: frm, limit: 10 },
        },
      });
      if (error) throw new Error(en.configAudit.loadError);
      return data ?? [];
    },
  });
}
