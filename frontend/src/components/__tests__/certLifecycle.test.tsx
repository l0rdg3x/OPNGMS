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

const BASE = "http://localhost:3000/api/tenants/t1/devices/d1/log-forwarding";
const enabledBody = {
  device_id: "d1", enabled: true, cert_serial: "ab", cert_fingerprint: "deadbeefcafe",
  provisioned_at: "2026-06-01T00:00:00Z", cert_not_after: "2027-01-01T00:00:00Z",
  last_log_at: "2026-06-01T10:00:00Z", revoked_at: null,
};

describe("LogForwardingCard cert lifecycle", () => {
  it("rotates the certificate", async () => {
    let rotated = false;
    server.use(
      http.get(BASE, () => HttpResponse.json(enabledBody)),
      http.post(`${BASE}/rotate`, () => { rotated = true; return HttpResponse.json(enabledBody); }),
    );
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "operator"));
    await userEvent.click(await screen.findByTestId("lf-rotate"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(rotated).toBe(true));
  });

  it("revokes and shows the Revoked badge", async () => {
    let enabled = true;
    server.use(
      http.get(BASE, () => HttpResponse.json({ ...enabledBody, enabled,
        revoked_at: enabled ? null : "2026-06-02T00:00:00Z" })),
      http.post(`${BASE}/revoke`, () => { enabled = false; return HttpResponse.json({
        ...enabledBody, enabled: false, revoked_at: "2026-06-02T00:00:00Z" }); }),
    );
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "operator"));
    await userEvent.click(await screen.findByTestId("lf-revoke"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));
    await waitFor(() => expect(screen.getByTestId("lf-status")).toHaveTextContent(/revoked/i));
  });

  it("hides rotate/revoke for read_only", async () => {
    server.use(http.get(BASE, () => HttpResponse.json(enabledBody)));
    renderWithProviders(withTenant(<LogForwardingCard deviceId="d1" />, "read_only"));
    expect(await screen.findByTestId("lf-status")).toBeInTheDocument();
    expect(screen.queryByTestId("lf-rotate")).toBeNull();
    expect(screen.queryByTestId("lf-revoke")).toBeNull();
  });
});
