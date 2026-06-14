import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { ReportPerimeterToggles } from "../ReportPerimeterToggles";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

describe("ReportPerimeterToggles", () => {
  it("PATCHes the device with both toggle values when one is changed", async () => {
    let body: { report_perimeter?: Record<string, boolean> } | null = null;
    server.use(
      http.patch("/api/tenants/t1/devices/d1", async ({ request }) => {
        body = (await request.json()) as { report_perimeter?: Record<string, boolean> };
        return HttpResponse.json({ id: "d1", report_perimeter: body!.report_perimeter });
      }),
    );
    renderWithProviders(
      <ReportPerimeterToggles
        tenantId="t1"
        deviceId="d1"
        value={{ failed_logins: true, firewall_blocks: true }}
      />,
    );
    const sw = await screen.findByTestId("report-toggle-firewall_blocks");
    expect(sw).toBeChecked();
    await userEvent.click(sw);
    // both current values are sent; only firewall_blocks flips to false
    await waitFor(() =>
      expect(body).toEqual({ report_perimeter: { failed_logins: true, firewall_blocks: false } }),
    );
  });
});
