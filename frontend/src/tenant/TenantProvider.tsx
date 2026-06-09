import { createContext, type ReactNode, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

export interface MyTenant {
  id: string;
  name: string;
  slug: string;
  role: string | null;
}

interface TenantState {
  tenants: MyTenant[];
  activeId: string | null;
  setActiveId: (id: string) => void;
  loading: boolean;
}

// eslint-disable-next-line react-refresh/only-export-components
export const TenantContext = createContext<TenantState>({
  tenants: [],
  activeId: null,
  setActiveId: () => {},
  loading: true,
});

export function TenantProvider({ children }: { children: ReactNode }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const { data, isLoading } = useQuery({
    queryKey: ["my-tenants"],
    queryFn: async (): Promise<MyTenant[]> => {
      const { data } = await api.GET("/api/me/tenants");
      return (data as MyTenant[]) ?? [];
    },
  });
  const tenants = data ?? [];
  const effectiveActive = activeId ?? tenants[0]?.id ?? null;
  return (
    <TenantContext.Provider
      value={{ tenants, activeId: effectiveActive, setActiveId, loading: isLoading }}
    >
      {children}
    </TenantContext.Provider>
  );
}
