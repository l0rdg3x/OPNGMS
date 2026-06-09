import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { DevicesPage } from "../DevicesPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "Alpha", slug: "alpha", role: "tenant_admin" }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

describe("DevicesPage", () => {
  it("lists devices for the active tenant", async () => {
    server.use(
      http.get("/api/tenants/t1/devices", () =>
        HttpResponse.json([
          {
            id: "d1", tenant_id: "t1", name: "fw-edge", base_url: "https://fw",
            verify_tls: true, tls_fingerprint: null, site: null, tags: [],
            status: "reachable", last_seen: null, firmware_version: "24.7",
            created_at: "2026-06-09T00:00:00Z", updated_at: "2026-06-09T00:00:00Z",
          },
        ]),
      ),
    );
    renderWithProviders(withTenant(<DevicesPage />));
    expect(await screen.findByText("fw-edge")).toBeInTheDocument();
    expect(screen.getByText("reachable")).toBeInTheDocument();
  });
});
