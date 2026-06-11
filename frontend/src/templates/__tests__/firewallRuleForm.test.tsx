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
import { FirewallRuleForm } from "../FirewallRuleForm";

type RuleBody = { payload: Record<string, string> };

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

const RULE_MODEL = {
  fields: [
    {
      path: "action",
      label: "action",
      control: "select",
      options: [
        { value: "pass", label: "Pass" },
        { value: "block", label: "Block" },
      ],
      value: "pass",
    },
    { path: "description", label: "description", control: "text", value: "" },
  ],
  interfaces: [{ value: "wan", label: "WAN" }],
};

// Capture the latest controlled value so the test can assert the payload after interactions.
let latest: RuleBody = { payload: {} };

function Harness() {
  const [v, setV] = useState<RuleBody>({ payload: {} });
  useEffect(() => {
    latest = v;
  }, [v]);
  return <FirewallRuleForm value={v} onChange={setV} />;
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
      "/api/tenants/t1/devices/d1/opnsense/firewall/rule-model",
      () => HttpResponse.json(RULE_MODEL),
    ),
  );
}

/** Drive the device Select and click Load, leaving the auto-form rendered. */
async function pickDeviceAndLoad() {
  await userEvent.click(await screen.findByTestId("fw-device"));
  await userEvent.click(await screen.findByText("fw1"));
  await userEvent.click(screen.getByTestId("fw-load"));
}

describe("FirewallRuleForm", () => {
  beforeEach(() => {
    latest = { payload: {} };
  });

  it("loads the rule model and renders the auto-form fields", async () => {
    mockHappyPath();
    renderHarness();

    await pickDeviceAndLoad();

    const action = await screen.findByTestId("fw-field-action");
    const description = await screen.findByTestId("fw-field-description");
    expect(action).toBeInTheDocument();
    expect(description).toBeInTheDocument();

    // Payload seeded from the model defaults.
    expect(latest.payload.action).toBe("pass");
    expect(latest.payload.description).toBe("");
  });

  it("typing into a text field updates the payload via onChange", async () => {
    mockHappyPath();
    renderHarness();

    await pickDeviceAndLoad();

    const description = await screen.findByTestId("fw-field-description");
    await userEvent.type(description, "block-telnet");
    expect(latest.payload.description).toBe("block-telnet");
  });
});
