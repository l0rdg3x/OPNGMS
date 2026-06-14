import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";

import { RetentionCard } from "../../retention/RetentionCard";
import { RuntimeSettingsSection } from "../../admin/RuntimeSettingsSection";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

// ---------------------------------------------------------------------------
// Helper: wrap with a TenantContext that pins an active tenant + role.
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

const RETENTION_URL = "http://localhost:3000/api/tenants/t1/retention";

const defaultsOnly = {
  overrides: {},
  defaults: { perimeter: 30, events: 90, metrics: 365 },
};

describe("RetentionCard — per-tenant", () => {
  it("renders inherit hints from the global defaults", async () => {
    server.use(http.get(RETENTION_URL, () => HttpResponse.json(defaultsOnly)));

    renderWithProviders(withTenant(<RetentionCard />));

    // The three NumberInputs render; with no overrides they show the inherit hint.
    expect(await screen.findByTestId("retention-perimeter")).toBeInTheDocument();
    expect(screen.getByTestId("retention-events")).toBeInTheDocument();
    expect(screen.getByTestId("retention-metrics")).toBeInTheDocument();

    // Each field surfaces the inherited global default as the hint text.
    expect(screen.getByText("Inherit global: 30")).toBeInTheDocument();
    expect(screen.getByText("Inherit global: 90")).toBeInTheDocument();
    expect(screen.getByText("Inherit global: 365")).toBeInTheDocument();
  });

  it("seeds the inputs from existing overrides", async () => {
    server.use(
      http.get(RETENTION_URL, () =>
        HttpResponse.json({ overrides: { perimeter: 14 }, defaults: defaultsOnly.defaults }),
      ),
    );

    renderWithProviders(withTenant(<RetentionCard />));

    const perimeter = await screen.findByTestId("retention-perimeter");
    expect(perimeter).toHaveValue("14");
    // Stores without an override stay empty (inherit).
    expect(screen.getByTestId("retention-events")).toHaveValue("");
  });

  it("saving an override sends a PUT with the value", async () => {
    let putBody: { values?: Record<string, unknown> } = {};
    server.use(
      http.get(RETENTION_URL, () => HttpResponse.json(defaultsOnly)),
      http.put(RETENTION_URL, async ({ request }) => {
        putBody = (await request.json()) as { values?: Record<string, unknown> };
        return HttpResponse.json({
          overrides: { perimeter: 7 },
          defaults: defaultsOnly.defaults,
        });
      }),
    );

    renderWithProviders(withTenant(<RetentionCard />));

    const perimeter = await screen.findByTestId("retention-perimeter");
    await userEvent.type(perimeter, "7");
    await userEvent.click(screen.getByTestId("retention-save"));

    await waitFor(() => expect(putBody.values).toBeDefined());
    // The edited store carries the override; untouched stores clear to inherit (null).
    expect(putBody.values).toMatchObject({ perimeter: 7, events: null, metrics: null });
  });

  it("clearing an override sends null for that store", async () => {
    let putBody: { values?: Record<string, unknown> } = {};
    server.use(
      http.get(RETENTION_URL, () =>
        HttpResponse.json({ overrides: { events: 120 }, defaults: defaultsOnly.defaults }),
      ),
      http.put(RETENTION_URL, async ({ request }) => {
        putBody = (await request.json()) as { values?: Record<string, unknown> };
        return HttpResponse.json({ overrides: {}, defaults: defaultsOnly.defaults });
      }),
    );

    renderWithProviders(withTenant(<RetentionCard />));

    const events = await screen.findByTestId("retention-events");
    expect(events).toHaveValue("120");

    // Empty the field, then save — that store should be sent as null (back to inherit).
    await userEvent.clear(events);
    await userEvent.click(screen.getByTestId("retention-save"));

    await waitFor(() => expect(putBody.values).toBeDefined());
    // All three are empty (only events had an override; perimeter/metrics were never set) → all null.
    expect(putBody.values).toEqual({ perimeter: null, events: null, metrics: null });
  });

  it("shows the load error when the GET fails", async () => {
    server.use(http.get(RETENTION_URL, () => HttpResponse.json({}, { status: 500 })));

    renderWithProviders(withTenant(<RetentionCard />));

    expect(await screen.findByTestId("retention-error")).toBeInTheDocument();
  });

  it("renders the warning Alert when a schedule now exceeds retention", async () => {
    server.use(
      http.get(RETENTION_URL, () =>
        HttpResponse.json({
          ...defaultsOnly,
          warnings: [
            {
              schedule_id: "11111111-1111-1111-1111-111111111111",
              frequency: "monthly",
              range_days: 30,
              bound: 14,
              limiting_store: "metrics",
            },
          ],
        }),
      ),
    );

    renderWithProviders(withTenant(<RetentionCard />));

    expect(await screen.findByTestId("retention-warnings")).toBeInTheDocument();
    // The interpolated line names the frequency, the window, the limiting store and the bound.
    expect(
      screen.getByText(/monthly schedule covers 30 days but metrics data is kept 14 days/),
    ).toBeInTheDocument();
  });

  it("renders no warning Alert when warnings is empty", async () => {
    server.use(http.get(RETENTION_URL, () => HttpResponse.json({ ...defaultsOnly, warnings: [] })));

    renderWithProviders(withTenant(<RetentionCard />));

    // The card loads (inputs present) but no warnings Alert is shown.
    expect(await screen.findByTestId("retention-perimeter")).toBeInTheDocument();
    expect(screen.queryByTestId("retention-warnings")).not.toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// Global runtime-settings group: the three retention knobs auto-render.
// ---------------------------------------------------------------------------
const RS = "http://localhost:3000/api/admin/settings";

describe("RuntimeSettingsSection — retention group", () => {
  const retentionSettings = {
    settings: [
      { key: "perimeter_retention_days", value: 30, default: 30, kind: "int", minimum: 1, maximum: 3650, group: "retention" },
      { key: "events_retention_days", value: 90, default: 90, kind: "int", minimum: 1, maximum: 3650, group: "retention" },
      { key: "metrics_retention_days", value: 365, default: 365, kind: "int", minimum: 1, maximum: 3650, group: "retention" },
    ],
  };

  it("renders the three retention knobs", async () => {
    server.use(http.get(RS, () => HttpResponse.json(retentionSettings)));

    renderWithProviders(<RuntimeSettingsSection />);

    expect(await screen.findByTestId("rs-perimeter_retention_days")).toBeInTheDocument();
    expect(screen.getByTestId("rs-events_retention_days")).toBeInTheDocument();
    expect(screen.getByTestId("rs-metrics_retention_days")).toBeInTheDocument();
    // The group heading renders from the new i18n label.
    expect(screen.getByText("Data retention")).toBeInTheDocument();
  });

  // After lowering a global default, the PUT may return impacted tenants — surface them. -------------
  async function lowerMetricsAndSave(impacts: unknown[]) {
    server.use(
      http.get(RS, () => HttpResponse.json(retentionSettings)),
      http.put(RS, () =>
        HttpResponse.json({ settings: retentionSettings.settings, retention_impacts: impacts }),
      ),
    );

    renderWithProviders(<RuntimeSettingsSection />);

    // Type a lower value so the knob is dirty and Save enables.
    const metrics = await screen.findByTestId("rs-metrics_retention_days");
    await userEvent.clear(metrics);
    await userEvent.type(metrics, "14");
    await userEvent.click(screen.getByTestId("runtime-settings-save"));
  }

  it("shows the impacted-tenants list when the PUT returns retention_impacts", async () => {
    await lowerMetricsAndSave([
      {
        tenant_id: "t-1",
        tenant_name: "Acme",
        store: "metrics",
        range_days: 30,
        bound: 14,
      },
    ]);

    expect(await screen.findByTestId("runtime-settings-impacts")).toBeInTheDocument();
    // The interpolated line names the tenant, the store, the needed range and the new bound.
    expect(
      screen.getByText(/Acme: a metrics report schedule needs 30 days but metrics is now kept 14 days/),
    ).toBeInTheDocument();
  });

  it("shows no impacts Alert when retention_impacts is empty", async () => {
    await lowerMetricsAndSave([]);

    // The save completes (the knob reverts to the committed value) but no impacts Alert appears.
    await waitFor(() =>
      expect(screen.queryByTestId("runtime-settings-impacts")).not.toBeInTheDocument(),
    );
  });
});
