import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { LogForwardingCard } from "../LogForwardingCard";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "operator") {
  return (
    <TenantContext.Provider value={{
      tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
      activeId: "t1", setActiveId: () => {}, loading: false,
    }}>{node}</TenantContext.Provider>
  );
}

const STATUS = "http://localhost:3000/api/tenants/t1/devices/d1/log-forwarding";
const ENABLE = "http://localhost:3000/api/tenants/t1/devices/d1/log-forwarding/enable";

describe("LogForwardingCard", () => {
  it("shows status + liveness and enables on confirm", async () => {
    let enabled = false;
    server.use(
      http.get(STATUS, () => HttpResponse.json({
        device_id: "d1", enabled, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
        provisioned_at: enabled ? "2026-06-01T00:00:00Z" : null,
        cert_not_after: enabled ? "2027-01-01T00:00:00Z" : null,
        last_log_at: enabled ? new Date().toISOString() : null,
      })),
      http.post(ENABLE, () => { enabled = true; return HttpResponse.json({
        device_id: "d1", enabled: true, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
        provisioned_at: "2026-06-01T00:00:00Z", cert_not_after: "2027-01-01T00:00:00Z", last_log_at: null }); }),
    );
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "operator"));
    expect(await screen.findByTestId("lf-status")).toHaveTextContent(/disabled/i);
    await userEvent.click(screen.getByTestId("lf-enable"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(screen.getByTestId("lf-status")).toHaveTextContent(/enabled/i));
    expect(screen.getByTestId("lf-liveness")).toBeInTheDocument();
  });

  it("hides action buttons for read_only", async () => {
    server.use(http.get(STATUS, () => HttpResponse.json({
      device_id: "d1", enabled: true, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
      provisioned_at: "2026-06-01T00:00:00Z", cert_not_after: "2027-01-01T00:00:00Z",
      last_log_at: "2026-06-01T10:00:00Z" })));
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "read_only"));
    expect(await screen.findByTestId("lf-status")).toBeInTheDocument();
    expect(screen.queryByTestId("lf-enable")).toBeNull();
    expect(screen.queryByTestId("lf-disable")).toBeNull();
  });
});
