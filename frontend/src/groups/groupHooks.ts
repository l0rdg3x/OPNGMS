import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";

export type GroupOut = components["schemas"]["GroupOut"];
export type GroupGrantOut = components["schemas"]["GroupGrantOut"];
export type GroupIn = components["schemas"]["GroupIn"];
export type GroupUpdateIn = components["schemas"]["GroupUpdateIn"];
export type GroupGrantIn = components["schemas"]["GroupGrantIn"];

// The CSRF header on mutating requests is injected centrally by the openapi-fetch
// middleware in api/client.ts (it reads csrfToken() for POST/PUT/PATCH/DELETE), so
// these hooks don't set it by hand.

const groupsKey = ["groups"] as const;

export function useGroups() {
  return useQuery({
    queryKey: groupsKey,
    queryFn: async (): Promise<GroupOut[]> => {
      const { data, error } = await api.GET("/api/groups");
      if (error || !data) throw new Error("Failed to load groups");
      return data;
    },
  });
}

export function useCreateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: GroupIn): Promise<GroupOut> => {
      const { data, error } = await api.POST("/api/groups", { body });
      if (error || !data) throw new Error("Failed to create group");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useUpdateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: GroupUpdateIn }): Promise<GroupOut> => {
      const { data, error } = await api.PATCH("/api/groups/{group_id}", {
        params: { path: { group_id: id } },
        body,
      });
      if (error || !data) throw new Error("Failed to update group");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useDeleteGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string): Promise<void> => {
      const { error } = await api.DELETE("/api/groups/{group_id}", {
        params: { path: { group_id: id } },
      });
      if (error) throw new Error("Failed to delete group");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useSetGroupMembers() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, userIds }: { id: string; userIds: string[] }): Promise<GroupOut> => {
      const { data, error } = await api.PUT("/api/groups/{group_id}/members", {
        params: { path: { group_id: id } },
        body: { user_ids: userIds },
      });
      if (error || !data) throw new Error("Failed to set members");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useAddGroupGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: GroupGrantIn }): Promise<GroupGrantOut> => {
      const { data, error } = await api.POST("/api/groups/{group_id}/grants", {
        params: { path: { group_id: id } },
        body,
      });
      if (error || !data) throw new Error("Failed to add grant");
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupsKey }),
  });
}

export function useDeleteGroupGrant() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ id, grantId }: { id: string; grantId: string }): Promise<void> => {
      const { error } = await api.DELETE("/api/groups/{group_id}/grants/{grant_id}", {
        params: { path: { group_id: id, grant_id: grantId } },
      });
      if (error) throw new Error("Failed to delete grant");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: groupsKey }),
  });
}

// Tenants the caller can see; for a superadmin this is every tenant. Used for the
// per-tenant grant picker. Shares the ["my-tenants"] key with TenantProvider so the
// two queries dedupe rather than firing /api/me/tenants twice.
export type MyTenant = components["schemas"]["MyTenantOut"];

export function useAllTenants() {
  return useQuery({
    queryKey: ["my-tenants"] as const,
    queryFn: async (): Promise<MyTenant[]> => {
      const { data, error } = await api.GET("/api/me/tenants");
      if (error || !data) throw new Error("Failed to load tenants");
      return data;
    },
  });
}
