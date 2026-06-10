import { createContext, type ReactNode, useMemo, useState } from "react";
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
  const [storedId, setStoredId] = useState<string | null>(readPersistedId);

  const { data, isLoading } = useQuery({
    queryKey: ["my-tenants"],
    queryFn: async (): Promise<MyTenant[]> => {
      const { data } = await api.GET("/api/me/tenants");
      return (data as MyTenant[]) ?? [];
    },
  });
  const tenants = useMemo(() => data ?? [], [data]);

  function setActiveId(id: string) {
    persistId(id);
    setStoredId(id);
  }

  // Validate the stored id once the tenant list is known.
  // Derive the effective active id without setState-in-effect: if the stored
  // id is not in the list we fall back to the first tenant.  We also update
  // localStorage to reflect the fallback (pure side-effect, not a re-render).
  const effectiveActive = useMemo(() => {
    if (isLoading || tenants.length === 0) return storedId ?? null;
    if (storedId && tenants.some((t) => t.id === storedId)) return storedId;
    const fallback = tenants[0]?.id ?? null;
    if (fallback) persistId(fallback);
    return fallback;
  }, [isLoading, tenants, storedId]);

  return (
    <TenantContext.Provider
      value={{ tenants, activeId: effectiveActive, setActiveId, loading: isLoading }}
    >
      {children}
    </TenantContext.Provider>
  );
}
