import { beforeEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { MemoryRouter } from "react-router-dom";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { MonitTestForm } from "../MonitTestForm";

type MonitBody = { payload: Record<string, string> };

const DEVICES = [
  {
    id: "d1",
    name: "fw1",
    tenant_id: "t1",
    base_url: "https://x",
    verify_tls: true,
    tls_fingerprint: null,
    site: null,
    tags: [],
    status: "reachable",
    last_seen: null,
    firmware_version: null,
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z",
  },
];

const TEST_MODEL = {
  fields: [
    { path: "name", label: "name", control: "text", value: "" },
    {
      path: "action",
      label: "action",
      control: "select",
      options: [
        { value: "alert", label: "alert" },
        { value: "restart", label: "restart" },
      ],
      value: "",
    },
  ],
};

// Capture the latest controlled value so the test can assert the payload after interactions.
let latest: MonitBody = { payload: {} };

function Harness() {
  const [v, setV] = useState<MonitBody>({ payload: {} });
  useEffect(() => {
    latest = v;
  }, [v]);
  return <MonitTestForm value={v} onChange={setV} />;
}

/** Shared providers wrapper. */
function makeWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <I18nProvider>
        <MantineProvider>
          <QueryClientProvider client={qc}>
            <TenantContext.Provider
              value={{
                tenants: [{ id: "t1", name: "Tenant A", slug: "a", role: "tenant_admin" }],
                activeId: "t1",
                setActiveId: () => {},
                loading: false,
              }}
            >
              <MemoryRouter>{children}</MemoryRouter>
            </TenantContext.Provider>
          </QueryClientProvider>
        </MantineProvider>
      </I18nProvider>
    );
  }
  return Wrapper;
}

function renderHarness() {
  return render(<Harness />, { wrapper: makeWrapper() });
}

function mockHappyPath() {
  server.use(
    http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
    http.get(
      "/api/tenants/t1/devices/d1/opnsense/monit/test-model",
      () => HttpResponse.json(TEST_MODEL),
    ),
  );
}

/** Drive the device Select and click Load, leaving the auto-form rendered. */
async function pickDeviceAndLoad() {
  await userEvent.click(await screen.findByTestId("monit-device"));
  await userEvent.click(await screen.findByText("fw1"));
  await userEvent.click(screen.getByTestId("monit-load"));
}

describe("MonitTestForm", () => {
  beforeEach(() => {
    latest = { payload: {} };
  });

  it("loads the test model and renders the auto-form fields", async () => {
    mockHappyPath();
    renderHarness();

    await pickDeviceAndLoad();

    const name = await screen.findByTestId("monit-field-name");
    const action = await screen.findByTestId("monit-field-action");
    expect(name).toBeInTheDocument();
    expect(action).toBeInTheDocument();
  });

  it("typing into the name field updates the payload via onChange", async () => {
    mockHappyPath();
    renderHarness();

    await pickDeviceAndLoad();

    const name = await screen.findByTestId("monit-field-name");
    await userEvent.type(name, "CPUHigh");
    expect(latest.payload.name).toBe("CPUHigh");
  });

  it("toggling the attach-to-system checkbox sets attach_to_system to '1'", async () => {
    mockHappyPath();
    renderHarness();

    await pickDeviceAndLoad();

    const attach = await screen.findByTestId("monit-attach-system");
    await userEvent.click(attach);
    expect(latest.payload.attach_to_system).toBe("1");
  });
});
