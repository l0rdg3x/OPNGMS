import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type SmtpOut = components["schemas"]["SmtpSettingsOut"];
export type SmtpIn = components["schemas"]["SmtpSettingsIn"];
export type SmtpTestIn = components["schemas"]["SmtpTestIn"];

const smtpKey = () => ["smtp-settings"] as const;

export function useSmtpSettings() {
  return useQuery({
    queryKey: smtpKey(),
    queryFn: async (): Promise<SmtpOut> => {
      const { data, error } = await api.GET("/api/admin/smtp");
      if (error || !data) throw new Error("Failed to load SMTP settings");
      return data;
    },
  });
}

export function useUpdateSmtpSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: SmtpIn): Promise<SmtpOut> => {
      const { data, error } = await api.PUT("/api/admin/smtp", { body });
      if (error || !data) throw new Error("Failed to save SMTP settings");
      return data;
    },
    onSuccess: (data) => qc.setQueryData(smtpKey(), data),
  });
}

export function useTestSmtp() {
  return useMutation({
    mutationFn: async (body: SmtpTestIn) => {
      const { data, error } = await api.POST("/api/admin/smtp/test", { body });
      if (error || !data) throw new Error("Test send failed");
      return data;
    },
  });
}
