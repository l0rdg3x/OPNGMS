// frontend/src/catalog/__tests__/catalogHooks.test.tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { I18nProvider } from "../../i18n";
import { useCatalogModel } from "../catalogHooks";
import { useDeviceCatalog } from "../catalogHooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return (
    <I18nProvider>
      <TenantContext.Provider
        value={{ tenants: [], activeId: "t1", setActiveId: () => {}, loading: false }}>
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </TenantContext.Provider>
    </I18nProvider>
  );
}

describe("useCatalogModel", () => {
  it("loads a model with live values", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound", () =>
        HttpResponse.json({
          model: { id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
                   fields: [{ path: "general.enabled", type: "bool" }], grids: [], pages: [] },
          values: { "general.enabled": "1" }, grids: {}, reachable: true, read_only: false,
        })),
    );
    const { result } = renderHook(() => useCatalogModel("d1", "unbound"), { wrapper });
    await waitFor(() => expect(result.current.data?.reachable).toBe(true));
    expect(result.current.data?.values["general.enabled"]).toBe("1");
  });
});

describe("useDeviceCatalog", () => {
  it("returns the menu tree", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog", () =>
        HttpResponse.json({
          resolved_version: "26.1.8", models: {},
          menu: [{ id: "Services", label: "Services", order: 50,
                   children: [{ id: "Unbound", label: "Unbound DNS", order: 0,
                                children: [{ id: "General", label: "General", order: 10,
                                             url: "/ui/unbound/general", model_id: "unbound" }] }] }],
        })),
    );
    const { result } = renderHook(() => useDeviceCatalog("d1"), { wrapper });
    await waitFor(() => expect(result.current.data?.menu?.[0].id).toBe("Services"));
  });
});
