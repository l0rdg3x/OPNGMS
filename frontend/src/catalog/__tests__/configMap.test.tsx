// frontend/src/catalog/__tests__/configMap.test.tsx
import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { CatalogEditorTab } from "../CatalogEditorTab";
import { ConfigMapTree } from "../ConfigMapTree";
import type { MapNode } from "../catalogTypes";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider value={{ tenants: [], activeId: "t1", setActiveId: () => {}, loading: false }}>
      {node}
    </TenantContext.Provider>
  );
}

const TREE: MapNode = {
  tag: "opnsense",
  path: "opnsense",
  attributes: {},
  editable: false,
  children: [
    {
      tag: "unboundplus",
      path: "opnsense/unboundplus",
      attributes: {},
      editable: true,
      catalog_model_id: "unbound.general",
      children: [],
    },
    {
      tag: "legacything",
      path: "opnsense/legacything",
      attributes: {},
      editable: false,
      children: [],
    },
  ],
};

describe("ConfigMapTree", () => {
  it("renders an 'Edit in catalog' control on an editable node and calls onEdit with its model id", () => {
    const onEdit = vi.fn();
    renderWithProviders(<ConfigMapTree root={TREE} onEdit={onEdit} />);
    const btn = screen.getByTestId("config-map-edit-unbound.general");
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveTextContent(/Edit in catalog/);
    fireEvent.click(btn);
    expect(onEdit).toHaveBeenCalledWith("unbound.general");
  });

  it("renders a read-only marker on a non-editable leaf", () => {
    renderWithProviders(<ConfigMapTree root={TREE} onEdit={vi.fn()} />);
    expect(screen.getByTestId("config-map-readonly-opnsense/legacything")).toHaveTextContent(
      /read-only \(no API\)/,
    );
  });
});

const CATALOG = {
  resolved_version: "26.1.8",
  models: {
    "unbound.general": {
      id: "unbound.general", title: "Unbound", model_root: "unbound", endpoints: {},
      fields: [{ path: "enabled", type: "bool" }], grids: [],
      pages: [{ id: "general", fields: ["enabled"] }], read_only: false,
    },
  },
  menu: [{ id: "Services", label: "Services", order: 50, children: [
    { id: "Unbound", label: "Unbound DNS", order: 0,
      url: "/ui/unbound/general", model_id: "unbound.general" }] }],
};

describe("CatalogEditorTab — Config map pane", () => {
  it("switches to the map pane, shows the stale banner, and 'Edit in catalog' jumps back to the model", async () => {
    server.use(
      http.get("*/api/tenants/t1/devices/d1/catalog", () => HttpResponse.json(CATALOG)),
      http.get("*/api/tenants/t1/devices/d1/config/map", () =>
        HttpResponse.json({
          source: "snapshot", reachable: false, taken_at: "2026-06-12T10:00:00Z",
          tree: {
            tag: "opnsense", path: "opnsense", attributes: {}, editable: false, children: [
              { tag: "unboundplus", path: "opnsense/unboundplus", attributes: {}, editable: true,
                catalog_model_id: "unbound.general", children: [] },
            ],
          },
        })),
      http.get("*/api/tenants/t1/devices/d1/catalog/models/unbound.general", () =>
        HttpResponse.json({ model: CATALOG.models["unbound.general"], values: { enabled: "1" },
                            grids: {}, field_options: {}, grid_field_options: {},
                            reachable: true, read_only: false })),
    );
    renderWithProviders(withTenant(<CatalogEditorTab deviceId="d1" baseUrl="https://1.2.3.4" />));
    await waitFor(() => expect(screen.getByTestId("catalog-pane-toggle")).toBeInTheDocument());

    // Switch to the Config map pane.
    fireEvent.click(screen.getByText("Config map"));
    // The snapshot source renders the stale banner with the substituted timestamp.
    await waitFor(() =>
      expect(screen.getByTestId("catalog-map-stale")).toHaveTextContent(/2026-06-12T10:00:00Z/));

    // Clicking "Edit in catalog" jumps back to the menu pane and opens the catalog model.
    fireEvent.click(screen.getByTestId("config-map-edit-unbound.general"));
    await waitFor(() =>
      expect(screen.getByTestId("catalog-field-enabled")).toBeInTheDocument());
  });
});
