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

describe("ReportSchedulePage", () => {
  it("creates the tenant (fleet) schedule", async () => {
    let putBody: unknown = null;
    server.use(
      http.get(LIST, () => HttpResponse.json([])),
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
});
