import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { TenantContext } from "../../tenant/TenantProvider";
import { renderWithProviders } from "../../test/utils";
import { ApplyProfileSection } from "../ApplyProfileSection";

// Replace DateTimePicker with a plain <input> so tests can drive it deterministically
// in jsdom without the full Mantine calendar portal (mirrors applyTemplate.test.tsx).
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

const P = {
  id: "p1",
  name: "Small",
  description: "",
  version: 1,
  template_ids: ["t1", "t2"],
  created_at: "2026-06-11T00:00:00Z",
  updated_at: "2026-06-11T00:00:00Z",
};

const PREVIEW = [
  { operation: "set", kind: "alias", target: "a", new: { name: "a", content: ["1.1.1.1"] } },
  { operation: "set", kind: "alias", target: "b", new: { name: "b", content: ["2.2.2.2"] } },
];

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

describe("ApplyProfileSection", () => {
  it("picks a profile and previews the ordered member set", async () => {
    server.use(http.get("/api/profiles", () => HttpResponse.json([P])));
    server.use(
      http.post("/api/tenants/t1/devices/d1/profiles/p1/preview", () =>
        HttpResponse.json(PREVIEW),
      ),
    );
    renderWithProviders(withTenant(<ApplyProfileSection deviceId="d1" />));

    // Pick the profile from the Select.
    const pick = await screen.findByTestId("prof-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("Small"));

    await userEvent.click(screen.getByTestId("prof-preview"));
    const out = await screen.findByTestId("prof-preview-out");
    // Both member previews render, in order.
    expect(within(out).getByText(/a: 1\.1\.1\.1/)).toBeInTheDocument();
    expect(within(out).getByText(/b: 2\.2\.2\.2/)).toBeInTheDocument();
    const lines = within(out).getAllByText(/^[ab]: /);
    expect(lines[0]).toHaveTextContent("a: 1.1.1.1");
    expect(lines[1]).toHaveTextContent("b: 2.2.2.2");
  });

  it("applies now and POSTs scheduled_at null", async () => {
    server.use(http.get("/api/profiles", () => HttpResponse.json([P])));
    const posted = vi.fn();
    server.use(
      http.post("/api/tenants/t1/devices/d1/profiles/p1/apply", async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json({ change_ids: ["c1", "c2"], status: "scheduled" });
      }),
    );
    renderWithProviders(withTenant(<ApplyProfileSection deviceId="d1" />));

    const pick = await screen.findByTestId("prof-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("Small"));

    await userEvent.click(screen.getByTestId("btn-prof-apply"));
    await userEvent.click(await screen.findByTestId("btn-prof-apply-now"));
    await waitFor(() =>
      expect(posted).toHaveBeenCalledWith(expect.objectContaining({ scheduled_at: null })),
    );
  });

  it("schedules an apply with an ISO scheduled_at", async () => {
    server.use(http.get("/api/profiles", () => HttpResponse.json([P])));
    const posted = vi.fn();
    server.use(
      http.post("/api/tenants/t1/devices/d1/profiles/p1/apply", async ({ request }) => {
        posted(await request.json());
        return HttpResponse.json({ change_ids: ["c2"], status: "scheduled" });
      }),
    );
    renderWithProviders(withTenant(<ApplyProfileSection deviceId="d1" />));

    const pick = await screen.findByTestId("prof-pick");
    await userEvent.click(pick);
    await userEvent.click(await screen.findByText("Small"));

    await userEvent.click(screen.getByTestId("btn-prof-apply"));
    const modal = await screen.findByTestId("prof-confirm-modal");
    // Schedule button disabled until a date is set.
    expect(screen.getByTestId("btn-prof-apply-schedule")).toBeDisabled();
    const input = within(modal).getByTestId("prof-schedule-picker-input");
    fireEvent.change(input, { target: { value: "2026-06-20 10:00:00" } });
    await waitFor(() => expect(screen.getByTestId("btn-prof-apply-schedule")).toBeEnabled());
    await userEvent.click(screen.getByTestId("btn-prof-apply-schedule"));
    await waitFor(() => {
      const call = posted.mock.calls[0]?.[0] as { scheduled_at: string };
      expect(call.scheduled_at).toMatch(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/);
      expect(call.scheduled_at).not.toContain(" ");
    });
  });
});
