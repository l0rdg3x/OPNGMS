// frontend/src/pages/__tests__/deviceDetailEditorTab.test.tsx
import { describe, expect, it } from "vitest";
import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { DeviceDetailPage } from "../DeviceDetailPage";

function withTenant(node: ReactNode) {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role: "tenant_admin" }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const device = {
  id: "d1", tenant_id: "t1", name: "fw1", base_url: "https://fw1", verify_tls: true,
  tls_fingerprint: null, site: null, tags: [], status: "unverified", last_seen: null,
  firmware_version: null, created_at: "2026-06-09T00:00:00Z", updated_at: "2026-06-09T00:00:00Z",
};

describe("DeviceDetailPage editor tab", () => {
  it("shows an Editor tab", async () => {
    server.use(http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)));
    renderWithProviders(
      withTenant(
        <Routes>
          <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
        </Routes>,
      ),
      { route: "/devices/d1" },
    );
    await screen.findByRole("heading", { name: "fw1" });
    expect(screen.getByText("Editor")).toBeInTheDocument();
  });
});
