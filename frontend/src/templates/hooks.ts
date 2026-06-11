import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { useT } from "../i18n";
import type { components } from "../api/schema";

export type Template = components["schemas"]["TemplateOut"];
export type TemplateIn = components["schemas"]["TemplateIn"];
export type TemplateUpdateIn = components["schemas"]["TemplateUpdateIn"];

export function useTemplates() {
  return useQuery({
    queryKey: ["templates"],
    queryFn: async (): Promise<Template[]> => {
      const { data, error } = await api.GET("/api/templates");
      if (error || !data) throw new Error("templates load failed");
      return data;
    },
  });
}

export function useCreateTemplate() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (body: TemplateIn): Promise<Template> => {
      const { data, error } = await api.POST("/api/templates", { body });
      if (error || !data) throw new Error(t.templates.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });
}

export function useUpdateTemplate() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: TemplateUpdateIn }): Promise<Template> => {
      const { data, error } = await api.PUT("/api/templates/{template_id}", {
        params: { path: { template_id: id } }, body });
      if (error || !data) throw new Error(t.templates.saveFailed);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  const t = useT();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/templates/{template_id}", {
        params: { path: { template_id: id } } });
      if (error) throw new Error(t.templates.saveFailed);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["templates"] }),
  });
}
