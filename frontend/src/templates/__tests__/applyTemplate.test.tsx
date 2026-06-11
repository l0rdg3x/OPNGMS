import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { ApplyTemplateTab } from "../ApplyTemplateTab";

// Replace DateTimePicker with a plain <input> so tests can drive it deterministically
// in jsdom without the full Mantine calendar portal (mirrors firmwareActions.test.tsx).
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

const T = {
  id: "tpl1",
  kind: "firewall_alias",
  name: "web",
  description: "",
  version: 1,
  body: { name: "web", type: "host", content: ["1.2.3.4"], description: "" },
  created_at: "2026-06-11T00:00:00Z",
  updated_at: "2026-06-11T00:00:00Z",
};

const PREVIEW = {
  operation: "set",
  kind: "firewall_alias",
  target: "web",
  new: { name: "web", content: ["1.2.3.4", "5.6.7.8"] },
};

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

describe("ApplyTemplateTab", () => {
  it("picks a template and previews the effective content", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    server.use(
      http.post("/api/tenants/t1/devices/d1/templates/tpl1/preview", () =>
        HttpResponse.json(PREVIEW),
      ),
    );
    renderWithProviders(withTenant(<ApplyTemplateTab deviceId="d1" />));

    // Pick the template from the Select.
    const pick = await screen.findByTestId("tpl-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("web"));

    await userEvent.click(screen.getByTestId("tpl-preview"));
    const out = await screen.findByTestId("tpl-preview-out");
    expect(within(out).getByText(/5\.6\.7\.8/)).toBeInTheDocument();
  });

  it("applies now and POSTs scheduled_at null", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const posted = vi.fn();
    server.use(
      http.post("/api/tenants/t1/devices/d1/templates/tpl1/apply", async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json({ change_id: "c1", status: "pending" });
      }),
    );
    renderWithProviders(withTenant(<ApplyTemplateTab deviceId="d1" />));

    const pick = await screen.findByTestId("tpl-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("web"));

    await userEvent.click(screen.getByTestId("btn-tpl-apply"));
    await userEvent.click(await screen.findByTestId("btn-tpl-apply-now"));
    await waitFor(() =>
      expect(posted).toHaveBeenCalledWith(expect.objectContaining({ scheduled_at: null })),
    );
  });

  it("schedules an apply with an ISO scheduled_at", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const posted = vi.fn();
    server.use(
      http.post("/api/tenants/t1/devices/d1/templates/tpl1/apply", async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json({ change_id: "c2", status: "scheduled" });
      }),
    );
    renderWithProviders(withTenant(<ApplyTemplateTab deviceId="d1" />));

    const pick = await screen.findByTestId("tpl-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("web"));

    await userEvent.click(screen.getByTestId("btn-tpl-apply"));
    const modal = await screen.findByTestId("tpl-confirm-modal");
    // Schedule button disabled until a date is set.
    expect(screen.getByTestId("btn-tpl-apply-schedule")).toBeDisabled();
    const input = within(modal).getByTestId("tpl-schedule-picker-input");
    fireEvent.change(input, { target: { value: "2026-06-20 10:00:00" } });
    await waitFor(() => expect(screen.getByTestId("btn-tpl-apply-schedule")).toBeEnabled());
    await userEvent.click(screen.getByTestId("btn-tpl-apply-schedule"));
    await waitFor(() => {
      const call = posted.mock.calls[0]?.[0] as { scheduled_at: string };
      expect(call.scheduled_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
      expect(call.scheduled_at).not.toContain(" ");
    });
  });

  it("saves this tenant's override (content split on newlines)", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const put = vi.fn();
    server.use(
      http.put("/api/tenants/t1/templates/tpl1/override", async ({ request }) => {
        put(await request.json());
        return HttpResponse.json({
          id: "o1",
          template_id: "tpl1",
          body_patch: { content: ["1.2.3.4", "9.9.9.9"] },
          updated_at: "2026-06-11T00:00:00Z",
        });
      }),
    );
    renderWithProviders(withTenant(<ApplyTemplateTab deviceId="d1" />));

    const pick = await screen.findByTestId("tpl-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("web"));

    const override = await screen.findByTestId("tpl-override");
    await userEvent.clear(override);
    await userEvent.type(override, "1.2.3.4\n9.9.9.9");
    await userEvent.click(screen.getByTestId("tpl-override-save"));
    await waitFor(() =>
      expect(put).toHaveBeenCalledWith(
        expect.objectContaining({ body_patch: { content: ["1.2.3.4", "9.9.9.9"] } }),
      ),
    );
  });
});
