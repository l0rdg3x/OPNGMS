import { screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { AttackerCountriesCard } from "../AttackerCountriesCard";
import { AttackerCountriesMap } from "../AttackerCountriesMap";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

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

describe("AttackerCountriesMap", () => {
  it("renders a choropleth (svg + country paths) for the given data", () => {
    const { container } = renderWithProviders(
      <AttackerCountriesMap
        data={[
          { code: "RU", count: 42, pct: 60 },
          { code: "CN", count: 18, pct: 25 },
        ]}
      />,
    );
    // The world topojson yields an <svg> with one <path> per country geometry.
    expect(container.querySelector("svg")).toBeInTheDocument();
    expect(container.querySelectorAll("path").length).toBeGreaterThan(0);
  });

  it("renders the muted empty state instead of a blank map on []", () => {
    const { container } = renderWithProviders(<AttackerCountriesMap data={[]} />);
    expect(
      screen.getByText(/no attacks recorded in this period/i),
    ).toBeInTheDocument();
    // No map is drawn when there is nothing to show.
    expect(container.querySelector("svg")).not.toBeInTheDocument();
  });

  it("is mounted in the card above the ranked list", async () => {
    server.use(
      http.get("/api/tenants/t1/attacker-countries", () =>
        HttpResponse.json([{ code: "RU", count: 42, pct: 60 }]),
      ),
    );
    const { container } = renderWithProviders(withTenant(<AttackerCountriesCard />));
    // The list shows the country once the fetch resolves.
    expect(await screen.findByText(/Russia/)).toBeInTheDocument();
    // The map (svg) is lazy-loaded behind Suspense, so it appears once its chunk resolves.
    await waitFor(() => expect(container.querySelector("svg")).toBeInTheDocument());
    // The map section heading ("Attack origins") is present.
    expect(screen.getByText(/Attack origins/i)).toBeInTheDocument();
  });
});
