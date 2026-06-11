import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { FirmwareActions } from "../FirmwareActions";

// Replace DateTimePicker with a plain <input> so tests can drive it deterministically
// in jsdom without the full Mantine calendar portal (which is unreliable in jsdom,
// as noted in src/config/__tests__/changesactions.test.tsx).
vi.mock("@mantine/dates", async (importActual) => {
  const actual = await importActual<typeof import("@mantine/dates")>();
  return {
    ...actual,
    DateTimePicker: ({
      onChange,
      "data-testid": testId,
    }: {
      onChange?: (value: string | null) => void;
      "data-testid"?: string;
    }) => (
      <div data-testid={testId}>
        <input
          data-testid={testId ? `${testId}-input` : undefined}
          onChange={(e) => onChange?.(e.target.value || null)}
        />
      </div>
    ),
  };
});

// Relative URL: client baseUrl is "" in tests.
const BASE = "/api/tenants/t1/devices/d1/firmware";

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

function listOnce(rows: unknown[] = []) {
  server.use(http.get(`${BASE}/actions`, () => HttpResponse.json(rows)));
}

describe("FirmwareActions", () => {
  it("runs a firmware check and shows the result", async () => {
    listOnce();
    server.use(
      http.post(`${BASE}/check`, () =>
        HttpResponse.json({
          status: "ok", updates: 3, download_size: "12M", needs_reboot: true, new_major: false,
        }),
      ),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.click(screen.getByTestId("btn-fw-check"));
    await screen.findByText(/Updates available/i);
    expect(screen.getByTestId("btn-fw-update")).toBeEnabled();
  });

  it("confirms and queues a firmware_update (run now)", async () => {
    listOnce();
    server.use(
      http.post(`${BASE}/check`, () =>
        HttpResponse.json({ status: "ok", updates: 1, download_size: "1M", needs_reboot: false, new_major: false }),
      ),
    );
    const posted = vi.fn();
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json(
          { id: "a1", kind: "firmware_update", target: "", status: "scheduled",
            scheduled_at: null, applied_at: null, result: {}, created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.click(screen.getByTestId("btn-fw-check"));
    await screen.findByText(/Updates available/i);
    await userEvent.click(screen.getByTestId("btn-fw-update"));
    // confirm modal -> Run now
    await userEvent.click(await screen.findByTestId("btn-fw-confirm-now"));
    await waitFor(() =>
      expect(posted).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "firmware_update", scheduled_at: null }),
      ),
    );
  });

  it("installs a plugin by name", async () => {
    listOnce();
    const posted = vi.fn();
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json(
          { id: "a2", kind: "plugin_install", target: "os-acme-client", status: "scheduled",
            scheduled_at: null, applied_at: null, result: {}, created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.type(screen.getByTestId("input-plugin-name"), "os-acme-client");
    await userEvent.click(screen.getByTestId("btn-plugin-install"));
    await userEvent.click(await screen.findByTestId("btn-fw-confirm-now"));
    await waitFor(() =>
      expect(posted).toHaveBeenCalledWith(
        expect.objectContaining({ kind: "plugin_install", target: "os-acme-client" }),
      ),
    );
  });

  it("renders recent actions from the list endpoint", async () => {
    listOnce([
      { id: "a9", kind: "plugin_remove", target: "os-foo", status: "done",
        scheduled_at: null, applied_at: null, result: { version: "26.1.9" },
        created_at: "2026-06-11T00:00:00Z" },
    ]);
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    const list = await screen.findByTestId("fw-actions-list");
    expect(within(list).getByText(/plugin_remove/)).toBeInTheDocument();
    expect(within(list).getByText(/done/)).toBeInTheDocument();
  });

  it("schedules a firmware_update with an ISO scheduled_at and shows Upgrade only when new_major", async () => {
    listOnce();
    server.use(
      http.post(`${BASE}/check`, () =>
        HttpResponse.json({ status: "ok", updates: 2, download_size: "5M", needs_reboot: true, new_major: true }),
      ),
    );
    const posted = vi.fn();
    server.use(
      http.post(`${BASE}/action`, async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json(
          { id: "a3", kind: "firmware_update", target: "", status: "scheduled",
            scheduled_at: "2026-06-20T10:00:00.000Z", applied_at: null, result: {},
            created_at: "2026-06-11T00:00:00Z" },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withTenant(<FirmwareActions deviceId="d1" />));
    await userEvent.click(screen.getByTestId("btn-fw-check"));
    await screen.findByText(/Updates available/i);
    // Upgrade button is present only because new_major is true.
    expect(screen.getByTestId("btn-fw-upgrade")).toBeInTheDocument();

    await userEvent.click(screen.getByTestId("btn-fw-update"));
    const modal = await screen.findByTestId("fw-confirm-modal");
    // Schedule button disabled until a date is set.
    expect(screen.getByTestId("btn-fw-confirm-schedule")).toBeDisabled();
    // Drive the mocked DateTimePicker's inner input with fireEvent.change (deterministic).
    const input = within(modal).getByTestId("fw-schedule-picker-input");
    fireEvent.change(input, { target: { value: "2026-06-20 10:00:00" } });
    // Schedule button should now be enabled.
    await waitFor(() => expect(screen.getByTestId("btn-fw-confirm-schedule")).toBeEnabled());
    await userEvent.click(screen.getByTestId("btn-fw-confirm-schedule"));
    await waitFor(() => {
      const call = posted.mock.calls[0]?.[0] as { scheduled_at: string };
      expect(call.scheduled_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
      expect(call.scheduled_at).not.toContain(" ");
    });
  });
});
