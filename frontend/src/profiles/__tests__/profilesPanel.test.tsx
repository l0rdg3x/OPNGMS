import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { AuthContext } from "../../auth/AuthProvider";
import { renderWithProviders } from "../../test/utils";
import { ProfilesPanel } from "../ProfilesPanel";

function withAuth(node: ReactNode, is_superadmin: boolean) {
  return (
    <AuthContext.Provider value={{
      me: { id: "1", email: "a@x.io", name: "A", is_superadmin },
      loading: false, refresh: vi.fn(), setMe: vi.fn() }}>
      {node}
    </AuthContext.Provider>
  );
}

const TPL_A = { id: "tpl-a", kind: "firewall_alias", name: "alpha", description: "a", version: 1,
  body: { name: "alpha", type: "host", content: ["1.2.3.4"], description: "a" },
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };
const TPL_B = { id: "tpl-b", kind: "firewall_alias", name: "bravo", description: "b", version: 1,
  body: { name: "bravo", type: "host", content: ["5.6.7.8"], description: "b" },
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };

const PROFILE = { id: "p1", name: "edge", description: "edge profile", template_ids: ["tpl-a"],
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };

describe("ProfilesPanel", () => {
  it("lists profiles with their member count", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([TPL_A, TPL_B])));
    server.use(http.get("/api/profiles", () => HttpResponse.json([PROFILE])));
    renderWithProviders(withAuth(<ProfilesPanel />, true));
    expect(await screen.findByText("edge")).toBeInTheDocument();
    expect(screen.getByText("edge profile")).toBeInTheDocument();
    expect(screen.getByText("1 templates")).toBeInTheDocument();
  });

  it("creates a profile with an ordered set of templates", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([TPL_A, TPL_B])));
    server.use(http.get("/api/profiles", () => HttpResponse.json([])));
    const posted = vi.fn();
    server.use(http.post("/api/profiles", async ({ request }) => {
      posted(await request.json());
      return HttpResponse.json(PROFILE, { status: 201 });
    }));

    renderWithProviders(withAuth(<ProfilesPanel />, true));

    await userEvent.click(await screen.findByTestId("prof-new"));
    await userEvent.type(screen.getByTestId("prof-name"), "branch");

    // Drive the Mantine MultiSelect: click its input to open the dropdown,
    // then click each option by label text — selection order = member order.
    const members = screen.getByTestId("prof-members");
    await userEvent.click(members);
    await userEvent.click(await screen.findByText("alpha"));
    await userEvent.click(members);
    await userEvent.click(await screen.findByText("bravo"));

    await userEvent.click(screen.getByTestId("prof-save"));

    await waitFor(() => expect(posted).toHaveBeenCalled());
    const body = posted.mock.calls[0][0];
    expect(body.name).toBe("branch");
    expect(body.template_ids).toEqual(["tpl-a", "tpl-b"]); // ordered member set
  });

  it("opens the edit modal prefilled from the profile", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([TPL_A, TPL_B])));
    server.use(http.get("/api/profiles", () => HttpResponse.json([PROFILE])));
    renderWithProviders(withAuth(<ProfilesPanel />, true));

    await userEvent.click(await screen.findByText("edge"));
    const row = screen.getByText("edge").closest("tr") as HTMLElement;
    await userEvent.click(within(row).getByText("Edit"));

    const nameInput = await screen.findByTestId("prof-name");
    await waitFor(() => expect(nameInput).toHaveValue("edge"));
    // the existing member (alpha) is shown as a pill in the MultiSelect
    const modal = nameInput.closest("[role='dialog']") as HTMLElement;
    expect(within(modal).getByText("alpha")).toBeInTheDocument();
  });
});
