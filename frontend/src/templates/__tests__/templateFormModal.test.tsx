import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { server } from "../../test/server";
import { I18nProvider } from "../../i18n";
import { TenantContext } from "../../tenant/TenantProvider";
import { TemplateFormModal } from "../TemplateFormModal";

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

const RULESETS = [{ filename: "a.rules", description: "Alpha", enabled: "0" }];

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

const MONIT_TEST_MODEL = {
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

function renderModal() {
  return render(
    <TemplateFormModal opened={true} onClose={vi.fn()} editing={null} />,
    { wrapper: makeWrapper() },
  );
}

describe("TemplateFormModal — suricata_ruleset", () => {
  it("creates a suricata_ruleset template with the selected rulesets", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/ids/rulesets",
        () => HttpResponse.json(RULESETS),
      ),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json(
          { id: "x", kind: "suricata_ruleset", name: "Baseline IDS", version: 1 },
          { status: 201 },
        );
      }),
    );

    renderModal();

    // Name.
    await userEvent.type(screen.getByTestId("tpl-name"), "Baseline IDS");

    // Switch the kind to Suricata/IDS rulesets (Mantine Select: click input, then option).
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Suricata/IDS rulesets"));

    // The IDS form is now shown: pick device + load + pick a ruleset.
    await userEvent.click(await screen.findByTestId("ids-device"));
    await userEvent.click(await screen.findByText("fw1"));
    await userEvent.click(screen.getByTestId("ids-load"));

    const rulesets = await screen.findByTestId("ids-rulesets");
    await userEvent.click(rulesets);
    await userEvent.click(await screen.findByText("Alpha"));

    // Save and assert the captured POST body.
    await userEvent.click(screen.getByTestId("tpl-save"));

    await waitFor(() =>
      expect(capture).toHaveBeenCalledWith({
        kind: "suricata_ruleset",
        name: "Baseline IDS",
        description: "",
        body: { rulesets: ["a.rules"] },
      })
    );
  });
});

describe("TemplateFormModal — firewall_rule", () => {
  it("creates a firewall_rule template with the loaded rule fields as the body", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/firewall/rule-model",
        () => HttpResponse.json(RULE_MODEL),
      ),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json(
          { id: "x", kind: "firewall_rule", name: "Block telnet", version: 1 },
          { status: 201 },
        );
      }),
    );

    renderModal();

    // Name.
    await userEvent.type(screen.getByTestId("tpl-name"), "Block telnet");

    // Switch the kind to Firewall rule (Mantine Select: click input, then option).
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Firewall rule (Rules [new])"));

    // The firewall-rule form is now shown: pick device + load the model.
    await userEvent.click(await screen.findByTestId("fw-device"));
    await userEvent.click(await screen.findByText("fw1"));
    await userEvent.click(screen.getByTestId("fw-load"));

    // Set the action to Block.
    const action = await screen.findByTestId("fw-field-action");
    await userEvent.click(action);
    await userEvent.click(await screen.findByText("Block"));

    // Set a description.
    const description = await screen.findByTestId("fw-field-description");
    await userEvent.type(description, "block-telnet");

    // Save and assert the captured POST body.
    await userEvent.click(screen.getByTestId("tpl-save"));

    await waitFor(() =>
      expect(capture).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "firewall_rule",
          name: "Block telnet",
          description: "",
          body: expect.objectContaining({ action: "block", description: "block-telnet" }),
        }),
      )
    );
  });

  it("refuses to save a firewall_rule with an empty description (it is the rule identity)", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/firewall/rule-model",
        () => HttpResponse.json(RULE_MODEL),
      ),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json({ id: "x", kind: "firewall_rule", name: "n", version: 1 }, { status: 201 });
      }),
    );

    renderModal();
    await userEvent.type(screen.getByTestId("tpl-name"), "No description");
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Firewall rule (Rules [new])"));
    await userEvent.click(await screen.findByTestId("fw-device"));
    await userEvent.click(await screen.findByText("fw1"));
    await userEvent.click(screen.getByTestId("fw-load"));
    // description left empty -> save is blocked client-side, no POST is made.
    await screen.findByTestId("fw-field-action");
    await userEvent.click(screen.getByTestId("tpl-save"));
    await new Promise((r) => setTimeout(r, 50));
    expect(capture).not.toHaveBeenCalled();
  });
});

describe("TemplateFormModal — monit_test", () => {
  it("creates a monit_test template with the loaded test fields as the body", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/monit/test-model",
        () => HttpResponse.json(MONIT_TEST_MODEL),
      ),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json(
          { id: "x", kind: "monit_test", name: "CPUHigh", version: 1 },
          { status: 201 },
        );
      }),
    );

    renderModal();

    // Name.
    await userEvent.type(screen.getByTestId("tpl-name"), "CPUHigh");

    // Switch the kind to Monit health-check test (Mantine Select: click input, then option).
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Monit health-check test"));

    // The monit-test form is now shown: pick device + load the model.
    await userEvent.click(await screen.findByTestId("monit-device"));
    await userEvent.click(await screen.findByText("fw1"));
    await userEvent.click(screen.getByTestId("monit-load"));

    // Set the test name (the identity).
    const name = await screen.findByTestId("monit-field-name");
    await userEvent.type(name, "CPUHigh");

    // Set the action to restart.
    const action = await screen.findByTestId("monit-field-action");
    await userEvent.click(action);
    await userEvent.click(await screen.findByText("restart"));

    // Save and assert the captured POST body.
    await userEvent.click(screen.getByTestId("tpl-save"));

    await waitFor(() =>
      expect(capture).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "monit_test",
          name: "CPUHigh",
          description: "",
          body: expect.objectContaining({ name: "CPUHigh", action: "restart" }),
        }),
      )
    );
  });

  it("refuses to save a monit_test with an empty name (it is the test identity)", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.get(
        "/api/tenants/t1/devices/d1/opnsense/monit/test-model",
        () => HttpResponse.json(MONIT_TEST_MODEL),
      ),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json({ id: "x", kind: "monit_test", name: "n", version: 1 }, { status: 201 });
      }),
    );

    renderModal();
    await userEvent.type(screen.getByTestId("tpl-name"), "No name");
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Monit health-check test"));
    await userEvent.click(await screen.findByTestId("monit-device"));
    await userEvent.click(await screen.findByText("fw1"));
    await userEvent.click(screen.getByTestId("monit-load"));
    // name left empty -> save is blocked client-side, no POST is made.
    await screen.findByTestId("monit-field-action");
    await userEvent.click(screen.getByTestId("tpl-save"));
    await new Promise((r) => setTimeout(r, 50));
    expect(capture).not.toHaveBeenCalled();
  });
});

describe("TemplateFormModal — ids_policy", () => {
  it("creates an ids_policy template with the policy body", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json(
          { id: "x", kind: "ids_policy", name: "Drop ET malware", version: 1 },
          { status: 201 },
        );
      }),
    );

    renderModal();

    // Name.
    await userEvent.type(screen.getByTestId("tpl-name"), "Drop ET malware");

    // Switch the kind to Suricata/IDS policy (Mantine Select: click input, then option).
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Suricata/IDS policy"));

    // The policy form is now shown: set the description (the identity) + the new action.
    // "default" is unique to the new_action Select (not in the action MultiSelect), so it disambiguates.
    await userEvent.type(await screen.findByTestId("idspolicy-description"), "Drop ET malware");
    await userEvent.click(screen.getByTestId("idspolicy-newaction"));
    await userEvent.click(await screen.findByText("default"));

    // Save and assert the captured POST body.
    await userEvent.click(screen.getByTestId("tpl-save"));

    await waitFor(() =>
      expect(capture).toHaveBeenCalledWith(
        expect.objectContaining({
          kind: "ids_policy",
          name: "Drop ET malware",
          description: "",
          body: expect.objectContaining({ description: "Drop ET malware", new_action: "default" }),
        }),
      )
    );
  });

  it("refuses to save an ids_policy with an empty description (it is the policy identity)", async () => {
    const capture = vi.fn();
    server.use(
      http.get("/api/tenants/t1/devices", () => HttpResponse.json(DEVICES)),
      http.post("/api/templates", async ({ request }) => {
        capture(await request.json());
        return HttpResponse.json({ id: "x", kind: "ids_policy", name: "n", version: 1 }, { status: 201 });
      }),
    );

    renderModal();
    await userEvent.type(screen.getByTestId("tpl-name"), "No description");
    await userEvent.click(screen.getByTestId("tpl-kind"));
    await userEvent.click(await screen.findByText("Suricata/IDS policy"));
    // description left empty -> save is blocked client-side, no POST is made.
    await screen.findByTestId("idspolicy-description");
    await userEvent.click(screen.getByTestId("tpl-save"));
    await new Promise((r) => setTimeout(r, 50));
    expect(capture).not.toHaveBeenCalled();
  });
});
