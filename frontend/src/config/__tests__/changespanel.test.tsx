import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { ChangesPanel } from "../ChangesPanel";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const change = {
  id: "c1",
  device_id: "d1",
  kind: "alias",
  operation: "set",
  target: "web_servers",
  status: "draft",
  scheduled_at: null,
  applied_at: null,
  created_at: "2026-06-10T10:00:00Z",
};

const CHANGES_URL = "/api/tenants/t1/devices/d1/config/changes";

describe("ChangesPanel", () => {
  it("renders the change rows with a status badge and a propose button (tenant_admin)", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([change])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    expect(await screen.findByText("web_servers")).toBeInTheDocument();
    expect(screen.getByText("alias")).toBeInTheDocument();
    // status badge
    expect(screen.getByText("draft")).toBeInTheDocument();
    // propose button visible for an editor role
    expect(
      screen.getByRole("button", { name: /propose alias change/i }),
    ).toBeInTheDocument();
  });

  it("hides the propose button for a read_only tenant", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([change])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "read_only"));

    expect(await screen.findByText("web_servers")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /propose alias change/i }),
    ).not.toBeInTheDocument();
  });

  it("shows the empty-state when there are no changes", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "tenant_admin"));

    expect(await screen.findByText(/no pending changes/i)).toBeInTheDocument();
  });
});
