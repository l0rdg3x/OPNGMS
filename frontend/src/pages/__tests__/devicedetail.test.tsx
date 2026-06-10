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
    await userEvent.click(screen.getByRole("button", { name: /test connection/i }));
    await waitFor(() => expect(screen.getByText(/reachable/i)).toBeInTheDocument());
  });

  it("shows the health section with charts and a range selector", async () => {
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
    // the Health tab is not active by default -> activate it first so the panel mounts
    await screen.findByRole("heading", { name: "fw1" });
    await userEvent.click(screen.getByRole("tab", { name: /Health/i }));
    // the health section chart titles appear
    expect(await screen.findByText(/CPU/i)).toBeInTheDocument();
    expect(await screen.findByText(/Memory/i)).toBeInTheDocument();
    // range selector present (Mantine SegmentedControl → radio input + label;
    // the "24h" text lives in a <span> inside the label)
    expect(screen.getByText("24h")).toBeInTheDocument();
  });

  it("does NOT delete without confirmation", async () => {
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
    // Click Delete — this should open the confirm modal, NOT fire the mutation yet
    await userEvent.click(screen.getByTestId("btn-delete"));
    // The confirm modal should be visible
    expect(await screen.findByTestId("confirm-modal")).toBeInTheDocument();
    // The delete has NOT been called yet
    expect(deleted).toBe(false);
    // Cancel — mutation must still not fire
    await userEvent.click(screen.getByTestId("confirm-cancel"));
    expect(deleted).toBe(false);
  });

  it("deletes the device after confirmation", async () => {
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
    await userEvent.click(screen.getByTestId("btn-delete"));
    // Confirm the modal
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(deleted).toBe(true));
  });
});
