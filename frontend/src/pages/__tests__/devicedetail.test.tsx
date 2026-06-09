import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { Route, Routes } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { DeviceDetailPage } from "../DeviceDetailPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

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

describe("DeviceDetailPage", () => {
  it("shows device and runs test-connection", async () => {
    server.use(
      http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
      http.post("/api/tenants/t1/devices/d1/test-connection", () =>
        HttpResponse.json({ status: "reachable", firmware_version: "24.7", error: null }),
      ),
    );
    renderWithProviders(
      withTenant(
        <Routes>
          <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
        </Routes>,
      ),
      { route: "/devices/d1" },
    );
    expect(await screen.findByRole("heading", { name: "fw1" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /testa connessione/i }));
    await waitFor(() => expect(screen.getByText(/reachable/i)).toBeInTheDocument());
  });

  it("mostra la sezione salute con grafici e selettore range", async () => {
    server.use(
      http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
      http.get("/api/tenants/t1/devices/d1/metrics", ({ request }) => {
        const url = new URL(request.url);
        const metric = url.searchParams.get("metric");
        return HttpResponse.json({
          metric,
          points: [
            { time: "2026-06-09T12:00:00Z", label: "", value: 12 },
            { time: "2026-06-09T12:05:00Z", label: "", value: 18 },
          ],
          last: [{ time: "2026-06-09T12:05:00Z", label: "", value: 18 }],
        });
      }),
    );
    renderWithProviders(
      withTenant(
        <Routes>
          <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
        </Routes>,
      ),
      { route: "/devices/d1" },
    );
    // i titoli dei grafici della sezione salute compaiono
    expect(await screen.findByText(/CPU/i)).toBeInTheDocument();
    expect(await screen.findByText(/Memoria/i)).toBeInTheDocument();
    // selettore range presente (SegmentedControl Mantine → input radio + label;
    // il testo "24h" è in uno <span> dentro la label)
    expect(screen.getByText("24h")).toBeInTheDocument();
  });

  it("deletes the device", async () => {
    let deleted = false;
    server.use(
      http.get("/api/tenants/t1/devices/d1", () => HttpResponse.json(device)),
      http.delete("/api/tenants/t1/devices/d1", () => {
        deleted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderWithProviders(
      withTenant(
        <Routes>
          <Route path="/devices/:deviceId" element={<DeviceDetailPage />} />
          <Route path="/devices" element={<div>device list</div>} />
        </Routes>,
      ),
      { route: "/devices/d1" },
    );
    await screen.findByRole("heading", { name: "fw1" });
    await userEvent.click(screen.getByRole("button", { name: /elimina/i }));
    await waitFor(() => expect(deleted).toBe(true));
  });
});
