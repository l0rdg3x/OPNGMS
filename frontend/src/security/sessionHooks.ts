import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";

export type SessionInfo = components["schemas"]["SessionInfo"];

const sessionsKey = () => ["sessions"];

export function useSessions() {
  return useQuery({
    queryKey: sessionsKey(),
    queryFn: async (): Promise<SessionInfo[]> => {
      const { data, error } = await api.GET("/api/sessions");
      if (error) throw new Error(en.sessions.loadError);
      return data ?? [];
    },
  });
}

export function useLogoutAll() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  return useMutation({
    mutationFn: async (): Promise<void> => {
      const { error } = await api.POST("/api/logout-all");
      if (error) throw new Error(en.sessions.logoutAllError);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: sessionsKey() });
      navigate("/login");
    },
  });
}
