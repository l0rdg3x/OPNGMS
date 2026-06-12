import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useTenant } from "../tenant/useTenant";
import type { components } from "../api/schema";

export type ScheduleOut = components["schemas"]["ReportScheduleOut"];
export type ScheduleIn = components["schemas"]["ReportScheduleIn"];

const key = (tid: string | null) => ["report-schedules", tid] as const;

export function useReportSchedules() {
  const { activeId } = useTenant();
  return useQuery({
    queryKey: key(activeId),
    enabled: !!activeId,
    queryFn: async (): Promise<ScheduleOut[]> => {
      const { data, error } = await api.GET("/api/tenants/{tenant_id}/report-schedules",
        { params: { path: { tenant_id: activeId! } } });
      if (error || !data) throw new Error("Failed to load schedules");
      return data;
    },
  });
}

export function useUpsertReportSchedule() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: ScheduleIn): Promise<ScheduleOut> => {
      const { data, error } = await api.PUT("/api/tenants/{tenant_id}/report-schedules",
        { params: { path: { tenant_id: activeId! } }, body });
      if (error || !data) throw new Error("Failed to save schedule");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key(activeId) }),
  });
}

export function useDeleteReportSchedule() {
  const { activeId } = useTenant();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.DELETE("/api/tenants/{tenant_id}/report-schedules/{schedule_id}",
        { params: { path: { tenant_id: activeId!, schedule_id: id } } });
      if (error) throw new Error("Failed to delete schedule");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: key(activeId) }),
  });
}

export function useSendScheduleNow() {
  const { activeId } = useTenant();
  return useMutation({
    mutationFn: async (id: string) => {
      const { error } = await api.POST("/api/tenants/{tenant_id}/report-schedules/{schedule_id}/send-now",
        { params: { path: { tenant_id: activeId!, schedule_id: id } } });
      if (error) throw new Error("Failed to send now");
    },
  });
}
