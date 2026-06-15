import { useMemo } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";
import { useTenant } from "../tenant/useTenant";

export type EventOut = components["schemas"]["EventOut"];
export type EventTopRow = components["schemas"]["EventTopRow"];

const DAY_MS = 24 * 60 * 60 * 1000;
const PAGE_SIZE = 50;

interface EventPage {
  items: EventOut[];
  next_cursor: string | null;
}

/**
 * Keyset-paginated timeline of one device's service (reliability) events for the active tenant.
 * Pages on the opaque `next_cursor` returned by `GET /events`; `fetchNextPage` is enabled while a
 * cursor is present. Reuses the existing typed events API — no reliability-specific endpoint.
 */
export function useReliabilityEvents(deviceId: string) {
  const { activeId } = useTenant();
  return useInfiniteQuery({
    queryKey: ["reliability-events", activeId, deviceId],
    enabled: !!activeId && !!deviceId,
    initialPageParam: null as string | null,
    queryFn: async ({ pageParam }): Promise<EventPage> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/events", {
        params: {
          path: { tenant_id: activeId! },
          query: {
            source: "service",
            device_id: deviceId,
            limit: PAGE_SIZE,
            after: pageParam ?? undefined,
          },
        },
      });
      if (error) throw new Error(en.reliability.loadError);
      return { items: data?.items ?? [], next_cursor: data?.next_cursor ?? null };
    },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/**
 * Fleet service-event counts over the last 24h, ranked by event name. The events API's
 * `/events/top` allow-list does NOT include `category` (only src_ip/dst_ip/name/action/severity),
 * so we aggregate by `name` — the per-event-type breakdown (reboot, service_crashed, …).
 */
export function useReliabilitySummary() {
  const { activeId } = useTenant();
  // Compute the range once per mount; a fresh `new Date()` each render would loop react-query.
  const frm = useMemo(() => new Date(new Date().getTime() - DAY_MS).toISOString(), []);
  return useQuery({
    queryKey: ["reliability-summary", activeId, frm],
    enabled: !!activeId,
    queryFn: async (): Promise<EventTopRow[]> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/events/top", {
        params: {
          path: { tenant_id: activeId! },
          query: { source: "service", field: "name", from: frm, limit: 10 },
        },
      });
      if (error) throw new Error(en.reliability.loadError);
      return data ?? [];
    },
  });
}
