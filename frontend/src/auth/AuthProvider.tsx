import { createContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";

export interface Me {
  id: string;
  email: string;
  name: string;
  is_superadmin: boolean;
  mfa_setup_required?: boolean;
}

interface AuthState {
  me: Me | null;
  loading: boolean;
  refresh: () => void;
  setMe: (me: Me) => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthState>({
  me: null,
  loading: true,
  refresh: () => {},
  setMe: () => {},
});

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<Me | null> => {
      const { data, response } = await api.GET("/api/me");
      if (response.status === 401) return null;
      return (data as Me) ?? null;
    },
  });
  return (
    <AuthContext.Provider
      value={{
        me: data ?? null,
        loading: isLoading,
        refresh: () => qc.invalidateQueries({ queryKey: ["me"] }),
        setMe: (me: Me) => qc.setQueryData(["me"], me),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}
