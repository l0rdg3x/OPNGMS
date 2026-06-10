import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ReportsPage } from "../ReportsPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

// ---------------------------------------------------------------------------
// Helper: wrap with a TenantContext that sets a specific role
// ---------------------------------------------------------------------------
function withTenant(node: ReactNode, role: string = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const REPORTS_URL = "http://localhost:3000/api/tenants/t1/reports";
const DOWNLOAD_URL = "http://localhost:3000/api/tenants/t1/reports/r1/download";

const oneReport = {
  id: "r1",
  kind: "on_demand",
  period_from: "2026-05-01T00:00:00Z",
  period_to: "2026-05-31T00:00:00Z",
  created_by: "u1",
  size: 12345,
  created_at: "2026-06-01T12:00:00Z",
};

// ---------------------------------------------------------------------------
// Mock browser download helpers so jsdom does not crash
// ---------------------------------------------------------------------------
const origCreateObjectURL = URL.createObjectURL;
const origRevokeObjectURL = URL.revokeObjectURL;

beforeEach(() => {
  URL.createObjectURL = vi.fn(() => "blob:x");
  URL.revokeObjectURL = vi.fn();
  HTMLAnchorElement.prototype.click = vi.fn();
});
afterEach(() => {
  URL.createObjectURL = origCreateObjectURL;
  URL.revokeObjectURL = origRevokeObjectURL;
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("ReportsPage — history rendering", () => {
  it("renders a report row from the GET list", async () => {
    server.use(http.get(REPORTS_URL, () => HttpResponse.json([oneReport])));

    renderWithProviders(withTenant(<ReportsPage />, "tenant_admin"));

    // The table should appear with the report's kind badge
    expect(await screen.findByText("on_demand")).toBeInTheDocument();
    // Size column should be formatted
    expect(await screen.findByText(/12\.1 KB/)).toBeInTheDocument();
  });

  it("shows empty-state when there are no reports", async () => {
    server.use(http.get(REPORTS_URL, () => HttpResponse.json([])));

    renderWithProviders(withTenant(<ReportsPage />, "tenant_admin"));

    expect(await screen.findByTestId("reports-empty")).toBeInTheDocument();
  });

  it("shows an error message when GET returns 500", async () => {
    server.use(
      http.get(REPORTS_URL, () => HttpResponse.json({ detail: "boom" }, { status: 500 })),
    );

    renderWithProviders(withTenant(<ReportsPage />, "tenant_admin"));

    expect(await screen.findByTestId("reports-error")).toBeInTheDocument();
  });
});

describe("ReportsPage — role gating", () => {
  it("hides generate card for read_only role but shows history", async () => {
    server.use(http.get(REPORTS_URL, () => HttpResponse.json([oneReport])));

    renderWithProviders(withTenant(<ReportsPage />, "read_only"));

    // Generate card must NOT be rendered
    expect(screen.queryByTestId("generate-card")).not.toBeInTheDocument();
    // History table IS shown
    expect(await screen.findByText("on_demand")).toBeInTheDocument();
  });

  it("shows the generate card for operator role", async () => {
    server.use(http.get(REPORTS_URL, () => HttpResponse.json([])));

    renderWithProviders(withTenant(<ReportsPage />, "operator"));

    expect(await screen.findByTestId("generate-card")).toBeInTheDocument();
    expect(screen.getByTestId("btn-generate")).toBeInTheDocument();
  });

  it("shows the generate card for tenant_admin role", async () => {
    server.use(http.get(REPORTS_URL, () => HttpResponse.json([])));

    renderWithProviders(withTenant(<ReportsPage />, "tenant_admin"));

    expect(await screen.findByTestId("generate-card")).toBeInTheDocument();
    expect(screen.getByTestId("btn-generate")).toBeInTheDocument();
  });
});

describe("ReportsPage — Download", () => {
  it("clicking Download triggers a fetch to the download endpoint", async () => {
    const downloadCalled = vi.fn();

    server.use(
      http.get(REPORTS_URL, () => HttpResponse.json([oneReport])),
      http.get(DOWNLOAD_URL, () => {
        downloadCalled();
        return new HttpResponse(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        });
      }),
    );

    renderWithProviders(withTenant(<ReportsPage />, "tenant_admin"));

    // Wait for the row to appear
    const downloadBtn = await screen.findByTestId("btn-download-r1");
    await userEvent.click(downloadBtn);

    await waitFor(() => expect(downloadCalled).toHaveBeenCalled());
  });
});

describe("ReportsPage — Generate", () => {
  it("clicking Generate POSTs to the reports endpoint", async () => {
    const generateCalled = vi.fn();

    server.use(
      http.get(REPORTS_URL, () => HttpResponse.json([])),
      http.post(REPORTS_URL, () => {
        generateCalled();
        return new HttpResponse(new Uint8Array([0x25, 0x50, 0x44, 0x46]), {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        });
      }),
    );

    renderWithProviders(withTenant(<ReportsPage />, "tenant_admin"));

    // Wait for generate card to appear
    const generateBtn = await screen.findByTestId("btn-generate");
    await userEvent.click(generateBtn);

    await waitFor(() => expect(generateCalled).toHaveBeenCalled());
  });
});
