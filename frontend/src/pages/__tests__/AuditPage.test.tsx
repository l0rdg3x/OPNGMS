import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuditPage } from "../AuditPage";
import { AppShell } from "../../components/AppShell";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const AUDIT = "http://localhost:3000/api/admin/audit";

function entry(over: Record<string, unknown> = {}) {
  return {
    id: "e1",
    ts: "2026-06-14T10:00:00Z",
    actor_user_id: "u1",
    actor_email: "admin@x.io",
    tenant_id: "t1",
    tenant_name: "Acme",
    action: "device.firmware.action",
    target_type: "device",
    target_id: "d1",
    ip: "203.0.113.7",
    details: { kind: "firmware_update" },
    ...over,
  };
}

// Capture every request's query params so tests can assert on filter/pagination wiring.
function auditHandler(seen: URLSearchParams[], total = 1) {
  return http.get(AUDIT, ({ request }) => {
    const params = new URL(request.url).searchParams;
    seen.push(params);
    const offset = Number(params.get("offset") ?? "0");
    const id = `e-${params.get("action") ?? "all"}-${offset}`;
    return HttpResponse.json({ items: [entry({ id })], total });
  });
}

describe("AuditPage", () => {
  it("renders rows from the API response", async () => {
    server.use(auditHandler([]));
    renderWithProviders(<AuditPage />);
    expect(await screen.findByText("admin@x.io")).toBeInTheDocument();
    expect(screen.getByText("Acme")).toBeInTheDocument();
    expect(screen.getByText("device.firmware.action")).toBeInTheDocument();
    // details JSON is rendered in the details cell
    expect(screen.getByText(/firmware_update/)).toBeInTheDocument();
  });

  it("refetches with the new query when a filter is applied", async () => {
    const seen: URLSearchParams[] = [];
    server.use(auditHandler(seen));
    renderWithProviders(<AuditPage />);
    await screen.findByText("admin@x.io");
    expect(seen.length).toBeGreaterThan(0);

    await userEvent.type(screen.getByTestId("audit-filter-action"), "login.success");
    await userEvent.click(screen.getByTestId("audit-apply"));

    await waitFor(() =>
      expect(seen.some((p) => p.get("action") === "login.success")).toBe(true),
    );
  });

  it("advances the offset when paginating to the next page", async () => {
    const seen: URLSearchParams[] = [];
    // total > page size so the Next button is enabled
    server.use(auditHandler(seen, 120));
    renderWithProviders(<AuditPage />);
    await screen.findByText("admin@x.io");
    // first request has offset 0 (or unset → treated as 0)
    expect(seen[0].get("offset") ?? "0").toBe("0");

    await userEvent.click(screen.getByTestId("audit-next"));

    await waitFor(() =>
      expect(seen.some((p) => p.get("offset") === "50")).toBe(true),
    );
  });

  it("exports CSV with the active filters", async () => {
    const cap: { url?: string } = {};
    const origCreate = URL.createObjectURL;
    const origRevoke = URL.revokeObjectURL;
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    try {
      server.use(
        auditHandler([]),
        http.get(`${AUDIT}/export.csv`, ({ request }) => {
          cap.url = request.url;
          return HttpResponse.text("ts,action\n", { headers: { "Content-Type": "text/csv" } });
        }),
      );
      renderWithProviders(<AuditPage />);
      await screen.findByText("admin@x.io");
      // Apply a filter first, then export: proves downloadAuditCsv forwards the active filters.
      await userEvent.type(screen.getByTestId("audit-filter-action"), "login.success");
      await userEvent.click(screen.getByTestId("audit-apply"));
      await userEvent.click(screen.getByTestId("audit-export"));
      await waitFor(() => expect(cap.url).toBeTruthy());
      const u = new URL(cap.url!);
      expect(u.pathname).toContain("/api/admin/audit/export.csv");
      expect(u.searchParams.get("action")).toBe("login.success");
    } finally {
      URL.createObjectURL = origCreate;
      URL.revokeObjectURL = origRevoke;
    }
  });
});

// ── Nav gating (mirror appshell.test.tsx) ──────────────────────────────────────
const me = { id: "1", email: "op@x.io", name: "Op", is_superadmin: false };

function withAuth(node: ReactNode, is_superadmin = false) {
  return (
    <AuthContext.Provider
      value={{ me: { ...me, is_superadmin }, loading: false, refresh: vi.fn(), setMe: vi.fn() }}
    >
      {node}
    </AuthContext.Provider>
  );
}

describe("AppShell — Audit nav entry", () => {
  function shellHandlers() {
    server.use(
      http.get("/api/me/tenants", () =>
        HttpResponse.json([{ id: "t1", name: "Alpha", slug: "alpha", role: "operator" }]),
      ),
      http.get("/api/tenants/t1/health", () =>
        HttpResponse.json({ total_devices: 0, by_status: {}, active_alerts: 0 }),
      ),
      http.get("/api/tenants/t1/alerts", () => HttpResponse.json([])),
      http.get("/api/tenants/t1/attacker-countries", () => HttpResponse.json([])),
    );
  }

  it("shows the Audit log nav link for a superadmin", async () => {
    shellHandlers();
    renderWithProviders(withAuth(<AppShell />, true));
    expect(await screen.findByRole("link", { name: /audit log/i })).toBeInTheDocument();
  });

  it("hides the Audit log nav link for a non-superadmin", async () => {
    shellHandlers();
    renderWithProviders(withAuth(<AppShell />, false));
    expect(await screen.findByText("op@x.io")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /audit log/i })).toBeNull();
  });
});
