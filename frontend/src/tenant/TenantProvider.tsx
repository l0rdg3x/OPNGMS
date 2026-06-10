import { createContext, type ReactNode, useEffect, useState } from "react";
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

const LS_KEY = "opngms.activeTenantId";

/** Read the persisted tenant id from localStorage (null if absent or unavailable). */
function readPersistedId(): string | null {
  try {
    return localStorage.getItem(LS_KEY);
  } catch {
    return null;
  }
}

/** Write the active tenant id to localStorage (silently ignores errors). */
function persistId(id: string): void {
  try {
    localStorage.setItem(LS_KEY, id);
  } catch {
    // storage quota or private-browsing restriction — ignore
  }
}

// eslint-disable-next-line react-refresh/only-export-components
export const TenantContext = createContext<TenantState>({
  tenants: [],
  activeId: null,
  setActiveId: () => {},
  loading: true,
});

export function TenantProvider({ children }: { children: ReactNode }) {
  // Initialise from localStorage so the selection survives page reloads.
  const [activeId, setActiveIdState] = useState<string | null>(readPersistedId);

  const { data, isLoading } = useQuery({
    queryKey: ["my-tenants"],
    queryFn: async (): Promise<MyTenant[]> => {
      const { data } = await api.GET("/api/me/tenants");
      return (data as MyTenant[]) ?? [];
    },
  });
  const tenants = data ?? [];

  // Validate the persisted id once the tenant list arrives; fall back to the
  // first available tenant if the stored id is no longer in the list.
  useEffect(() => {
    if (isLoading || tenants.length === 0) return;
    if (activeId && tenants.some((t) => t.id === activeId)) return; // still valid
    const fallback = tenants[0]?.id ?? null;
    setActiveIdState(fallback);
    if (fallback) persistId(fallback);
  }, [isLoading, tenants, activeId]);

  function setActiveId(id: string) {
    persistId(id);
    setActiveIdState(id);
  }

  const effectiveActive = activeId ?? tenants[0]?.id ?? null;
  return (
    <TenantContext.Provider
      value={{ tenants, activeId: effectiveActive, setActiveId, loading: isLoading }}
    >
      {children}
    </TenantContext.Provider>
  );
}
