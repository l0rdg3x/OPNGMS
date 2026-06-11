import { describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { DeviceDetailPage } from "../DeviceDetailPage";

// Relative URLs (client baseUrl is "" in tests). Routing + tenant mirror
// the existing src/pages/__tests__/devicedetail.test.tsx exactly.
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
  id: "d1", tenant_id: "t1", name: "fw1", base_url: "https://192.168.1.82", verify_tls: true,
  tls_fingerprint: null, site: null, tags: [], status: "reachable", last_seen: null,
  firmware_version: "26.1.9", created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z",
};

describe("DeviceDetailPage firmware tab", () => {
  it("shows a Firmware tab and the WebGUI link", async () => {
    server.use(
      http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
      http.get("/api/tenants/t1/devices/d1/firmware/actions", () => HttpResponse.json([])),
    );
    renderWithProviders(
      withTenant(
        <Routes>
          <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
        </Routes>,
      ),
      { route: "/devices/d1" },
    );
    // WebGUI link present on the Info tab
    expect((await screen.findByTestId("btn-webgui")).getAttribute("href")).toBe("https://192.168.1.82");
    // switch to the Firmware tab (inactive Tabs.Panel mounts lazily on activation)
    await userEvent.click(screen.getByRole("tab", { name: /Firmware/i }));
    expect(await screen.findByTestId("btn-fw-check")).toBeInTheDocument();
  });
});
