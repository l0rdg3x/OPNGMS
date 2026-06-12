import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ReportSchedulePage } from "../ReportSchedulePage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "tenant_admin") {
  return (
    <TenantContext.Provider value={{
      tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
      activeId: "t1", setActiveId: () => {}, loading: false,
    }}>{node}</TenantContext.Provider>
  );
}

const LIST = "http://localhost:3000/api/tenants/t1/report-schedules";
const DEVICES = "http://localhost:3000/api/tenants/t1/devices";

describe("ReportSchedulePage", () => {
  it("creates the tenant (fleet) schedule", async () => {
    let putBody: unknown = null;
    server.use(
      http.get(LIST, () => HttpResponse.json([])),
      http.get(DEVICES, () => HttpResponse.json([])),
      http.put(LIST, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ id: "11111111-1111-1111-1111-111111111111", device_id: null,
          enabled: true, frequency: "weekly", weekday: 0, hour: 4, recipients: ["a@x.io"],
          next_run_at: "2026-06-15T04:00:00Z", last_run_at: null });
      }),
    );
    renderWithProviders(withTenant(<ReportSchedulePage />, "tenant_admin"));
    await userEvent.type(await screen.findByTestId("fleet-recipients"), "a@x.io");
    await userEvent.click(screen.getByTestId("fleet-save"));
    await waitFor(() => expect((putBody as { frequency: string }).frequency).toBe("weekly"));
  });

  it("blocks non-admin roles", () => {
    server.use(http.get(LIST, () => HttpResponse.json([])));
    renderWithProviders(withTenant(<ReportSchedulePage />, "read_only"));
    expect(screen.getByTestId("schedule-forbidden")).toBeInTheDocument();
  });

  it("renders a per-device editor for each device and saves a device schedule", async () => {
    let putBody: { device_id?: string } = {};
    server.use(
      http.get(LIST, () => HttpResponse.json([])),
      http.get(DEVICES, () => HttpResponse.json([
        { id: "d1", name: "fw-1" },
        { id: "d2", name: "fw-2" },
      ])),
      http.put(LIST, async ({ request }) => {
        putBody = (await request.json()) as { device_id?: string };
        return HttpResponse.json({ id: "s1", device_id: putBody.device_id ?? null, enabled: true,
          frequency: "weekly", weekday: 0, hour: 4, recipients: ["a@x.io"],
          next_run_at: null, last_run_at: null });
      }),
    );
    renderWithProviders(withTenant(<ReportSchedulePage />, "tenant_admin"));
    await screen.findByTestId("device-schedule-row-d1");
    expect(screen.getByTestId("device-schedule-row-d2")).toBeInTheDocument();
    await userEvent.click(await screen.findByTestId("device-schedule-row-d1"));
    await userEvent.type(await screen.findByTestId("device-d1-recipients"), "a@x.io");
    await userEvent.click(screen.getByTestId("device-d1-save"));
    await waitFor(() => expect(putBody.device_id).toBe("d1"));
  });
});
