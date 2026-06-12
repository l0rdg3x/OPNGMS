import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { components } from "../api/schema";
import { en } from "../i18n/en";

export type MfaStatus = components["schemas"]["MfaStatusOut"];
export type SetupOut = components["schemas"]["SetupOut"];
export type RecoveryOut = components["schemas"]["RecoveryOut"];
export type MfaPolicy = components["schemas"]["MfaPolicyOut"];
export type UserOut = components["schemas"]["UserOut"];

const mfaStatusKey = () => ["mfa", "status"];
const mfaPolicyKey = () => ["mfa", "policy"];
const usersKey = () => ["users"];

/** Current user's MFA status (enabled? recovery codes remaining). */
export function useMfaStatus() {
  return useQuery({
    queryKey: mfaStatusKey(),
    queryFn: async (): Promise<MfaStatus> => {
      const { data, error } = await api.GET("/api/me/mfa");
      if (error || !data) throw new Error(en.mfa.statusError);
      return data;
    },
  });
}

/** Start enrollment: password re-auth → { otpauth_uri, secret }. */
export function useMfaSetup() {
  return useMutation({
    mutationFn: async (password: string): Promise<SetupOut> => {
      const { data, error } = await api.POST("/api/me/mfa/setup", { body: { password } });
      if (error || !data) throw new Error(en.mfa.setupError);
      return data;
    },
  });
}

/** Confirm enrollment with a TOTP code → returns the one-time recovery codes. */
export function useMfaConfirm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (code: string): Promise<RecoveryOut> => {
      const { data, error } = await api.POST("/api/me/mfa/confirm", { body: { code } });
      if (error || !data) throw new Error(en.mfa.confirmError);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: mfaStatusKey() }),
  });
}

/** Disable MFA (password re-auth). */
export function useMfaDisable() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (password: string): Promise<void> => {
      const { error } = await api.POST("/api/me/mfa/disable", { body: { password } });
      if (error) throw new Error(en.mfa.disableError);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: mfaStatusKey() }),
  });
}

/** Regenerate recovery codes (password re-auth) → returns the new set once. */
export function useMfaRegenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (password: string): Promise<RecoveryOut> => {
      const { data, error } = await api.POST("/api/me/mfa/recovery/regenerate", {
        body: { password },
      });
      if (error || !data) throw new Error(en.mfa.regenerateError);
      return data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: mfaStatusKey() }),
  });
}

/** Org-wide MFA enforcement policy (superadmin). */
export function useMfaPolicy() {
  return useQuery({
    queryKey: mfaPolicyKey(),
    queryFn: async (): Promise<MfaPolicy> => {
      const { data, error } = await api.GET("/api/admin/mfa-policy");
      if (error || !data) throw new Error(en.mfa.policyLoadError);
      return data;
    },
  });
}

export function useSetMfaPolicy() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (mode: string): Promise<MfaPolicy> => {
      const { data, error } = await api.PUT("/api/admin/mfa-policy", { body: { mode } });
      if (error || !data) throw new Error(en.mfa.policySaveError);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(mfaPolicyKey(), data);
    },
  });
}

/** All users (superadmin) — for the admin MFA-reset table. */
export function useUsers() {
  return useQuery({
    queryKey: usersKey(),
    queryFn: async (): Promise<UserOut[]> => {
      const { data, error } = await api.GET("/api/users");
      if (error) throw new Error(en.mfa.usersLoadError);
      return data ?? [];
    },
  });
}

/** Admin-reset a user's MFA (superadmin). */
export function useResetUserMfa() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (userId: string): Promise<void> => {
      const { error } = await api.POST("/api/users/{user_id}/mfa/reset", {
        params: { path: { user_id: userId } },
      });
      if (error) throw new Error(en.mfa.resetError);
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: usersKey() }),  // refresh the users table
  });
}
