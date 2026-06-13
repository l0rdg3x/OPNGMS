import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { GroupsPage } from "../GroupsPage";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const me = { id: "u-admin", email: "admin@x.io", name: "Admin", is_superadmin: true };

function withSuperadmin(node: ReactNode, is_superadmin = true) {
  return (
    <AuthContext.Provider
      value={{ me: { ...me, is_superadmin }, loading: false, refresh: vi.fn(), setMe: vi.fn() }}
    >
      {node}
    </AuthContext.Provider>
  );
}

const GROUPS = "http://localhost:3000/api/groups";
const USERS = "http://localhost:3000/api/users";
const TENANTS = "http://localhost:3000/api/me/tenants";

const group = {
  id: "g1",
  name: "Operators",
  description: "Front-line operators",
  member_ids: [] as string[],
  grants: [] as { id: string; all_tenants: boolean; tenant_id: string | null; role: string }[],
};

function baseHandlers(groups = [group]) {
  return [
    http.get(GROUPS, () => HttpResponse.json(groups)),
    http.get(USERS, () =>
      HttpResponse.json([
        { id: "u1", email: "alice@x.io", name: "Alice", is_superadmin: false, status: "active" },
      ]),
    ),
    http.get(TENANTS, () =>
      HttpResponse.json([{ id: "t1", name: "Alpha", slug: "alpha", role: null }]),
    ),
  ];
}

describe("GroupsPage", () => {
  it("blocks non-superadmins", () => {
    server.use(...baseHandlers());
    renderWithProviders(withSuperadmin(<GroupsPage />, false));
    expect(screen.getByTestId("groups-superadmin-gate")).toBeInTheDocument();
  });

  it("lists groups returned by the API", async () => {
    server.use(...baseHandlers());
    renderWithProviders(withSuperadmin(<GroupsPage />));
    expect(await screen.findByTestId("group-name-g1")).toHaveTextContent("Operators");
  });

  it("creates a group via POST /api/groups", async () => {
    let postBody: { name?: string; description?: string } = {};
    server.use(
      http.get(GROUPS, () => HttpResponse.json([])),
      http.get(USERS, () => HttpResponse.json([])),
      http.get(TENANTS, () => HttpResponse.json([])),
      http.post(GROUPS, async ({ request }) => {
        postBody = (await request.json()) as typeof postBody;
        return HttpResponse.json(
          { id: "g2", name: postBody.name, description: postBody.description ?? "", member_ids: [], grants: [] },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withSuperadmin(<GroupsPage />));
    await userEvent.type(await screen.findByTestId("group-new-name"), "Admins");
    await userEvent.click(screen.getByTestId("group-create"));
    await waitFor(() => expect(postBody.name).toBe("Admins"));
  });

  it("adds an all-tenants grant with the right body", async () => {
    let grantBody: { all_tenants?: boolean; tenant_id?: string | null; role?: string } = {};
    server.use(
      ...baseHandlers(),
      http.post("http://localhost:3000/api/groups/g1/grants", async ({ request }) => {
        grantBody = (await request.json()) as typeof grantBody;
        return HttpResponse.json(
          { id: "gr1", all_tenants: true, tenant_id: null, role: grantBody.role },
          { status: 201 },
        );
      }),
    );
    renderWithProviders(withSuperadmin(<GroupsPage />));
    await screen.findByTestId("group-card-g1");

    // Turn on "All tenants" (Switch renders a checkbox). The tenant picker should disappear.
    const card = screen.getByTestId("group-card-g1");
    await userEvent.click(within(card).getByTestId("grant-all-tenants-g1"));
    expect(within(card).queryByTestId("grant-tenant-g1")).not.toBeInTheDocument();

    await userEvent.click(within(card).getByTestId("grant-add-g1"));
    await waitFor(() => expect(grantBody.all_tenants).toBe(true));
    expect(grantBody.tenant_id).toBeUndefined();
    expect(grantBody.role).toBe("read_only");
  });
});
