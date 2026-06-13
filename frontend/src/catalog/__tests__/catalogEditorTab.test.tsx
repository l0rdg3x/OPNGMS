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
  menu: [{ id: "Services", label: "Services", order: 50, children: [
    { id: "Unbound", label: "Unbound DNS", order: 0, children: [
      { id: "General", label: "General", order: 10, url: "/ui/unbound/general", model_id: "unbound" }]}]}],
};

describe("CatalogEditorTab", () => {
  it("navigates the menu tree and opens a model", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog", () => HttpResponse.json(CATALOG)),
      http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound", () =>
        HttpResponse.json({ model: CATALOG.models.unbound, values: { "general.enabled": "1" },
                            grids: {}, field_options: {}, grid_field_options: {},
                            reachable: true, read_only: false })),
    );
    renderWithProviders(withTenant(<CatalogEditorTab deviceId="d1" baseUrl="https://1.2.3.4" />));
    await waitFor(() => expect(screen.getByText("Services")).toBeInTheDocument());
    // Categories start collapsed (OPNsense-like); a search query expands the tree (defaultOpened),
    // so the nested "General" leaf is reliably mounted before we click it (was flaky on a bare click).
    fireEvent.change(screen.getByTestId("catalog-search"), { target: { value: "general" } });
    fireEvent.click(await screen.findByText("General"));   // a menu leaf
    await waitFor(() => expect(screen.getByTestId("catalog-field-general.enabled")).toBeInTheDocument());
  });
});
