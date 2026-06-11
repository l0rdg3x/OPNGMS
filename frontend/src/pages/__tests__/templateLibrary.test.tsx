import { describe, expect, it, vi } from "vitest";
import { http, HttpResponse } from "msw";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { server } from "../../test/server";
import { AuthContext } from "../../auth/AuthProvider";
import { renderWithProviders } from "../../test/utils";
import { TemplateLibraryPage } from "../TemplateLibraryPage";

function withAuth(node: ReactNode, is_superadmin: boolean) {
  return (
    <AuthContext.Provider value={{
      me: { id: "1", email: "a@x.io", name: "A", is_superadmin },
      loading: false, refresh: vi.fn(), setMe: vi.fn() }}>
      {node}
    </AuthContext.Provider>
  );
}
const T = { id: "x1", kind: "firewall_alias", name: "web", description: "d", version: 1,
  body: { name: "web", type: "host", content: ["1.2.3.4"], description: "d" },
  created_at: "2026-06-11T00:00:00Z", updated_at: "2026-06-11T00:00:00Z" };

describe("TemplateLibraryPage", () => {
  it("shows the superadmin-only gate for non-superadmins", () => {
    renderWithProviders(withAuth(<TemplateLibraryPage />, false));
    expect(screen.getByTestId("tpl-superadmin-gate")).toBeInTheDocument();
    expect(screen.queryByTestId("tpl-new")).toBeNull();
  });

  it("lists templates and creates one (content parsed to a list)", async () => {
    server.use(http.get("/api/templates", () => HttpResponse.json([T])));
    const posted = vi.fn();
    server.use(http.post("/api/templates", async ({ request }) => {
      posted(await request.json());
      return HttpResponse.json(T, { status: 201 });
    }));
    renderWithProviders(withAuth(<TemplateLibraryPage />, true));
    expect(await screen.findByText("web")).toBeInTheDocument();
    await userEvent.click(screen.getByTestId("tpl-new"));
    await userEvent.type(screen.getByTestId("tpl-name"), "db");
    await userEvent.type(screen.getByTestId("tpl-content"), "10.0.0.1\n10.0.0.2");
    await userEvent.click(screen.getByTestId("tpl-save"));
    await waitFor(() => expect(posted).toHaveBeenCalled());
    const body = posted.mock.calls[0][0];
    expect(body.name).toBe("db");
    expect(body.body.content).toEqual(["10.0.0.1", "10.0.0.2"]);  // newlines -> list
  });
});
