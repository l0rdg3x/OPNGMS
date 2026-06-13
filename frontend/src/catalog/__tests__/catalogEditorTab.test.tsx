// frontend/src/catalog/__tests__/catalogEditorTab.test.tsx
import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { CatalogEditorTab } from "../CatalogEditorTab";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider value={{ tenants: [], activeId: "t1", setActiveId: () => {}, loading: false }}>
      {node}
    </TenantContext.Provider>
  );
}

const CATALOG = {
  resolved_version: "26.1.8",
  models: {
    unbound: { id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
               fields: [{ path: "general.enabled", type: "bool" }], grids: [],
               pages: [{ id: "general", fields: ["general.enabled"] }], read_only: false },
  },
};

describe("CatalogEditorTab", () => {
  it("lists models and opens one", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog", () => HttpResponse.json(CATALOG)),
      http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound", () =>
        HttpResponse.json({ model: CATALOG.models.unbound, values: { "general.enabled": "1" },
                            grids: {}, reachable: true, read_only: false })),
    );
    renderWithProviders(withTenant(<CatalogEditorTab deviceId="d1" />));
    await waitFor(() => expect(screen.getByText("Unbound")).toBeInTheDocument());
    fireEvent.click(screen.getByText("Unbound"));
    await waitFor(() => expect(screen.getByTestId("catalog-field-general.enabled")).toBeInTheDocument());
  });
});
