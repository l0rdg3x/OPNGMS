import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { AttackerCountriesCard } from "../AttackerCountriesCard";
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

describe("AttackerCountriesCard", () => {
  it("renders the returned countries with localized names", async () => {
    server.use(
      http.get("/api/tenants/t1/attacker-countries", () =>
        HttpResponse.json([
          { code: "RU", count: 42, pct: 60 },
          { code: "CN", count: 18, pct: 25 },
        ]),
      ),
    );
    renderWithProviders(withTenant(<AttackerCountriesCard />));
    // "RU" → "Russia" via Intl.DisplayNames (default en locale in tests).
    expect(await screen.findByText(/Russia/)).toBeInTheDocument();
    expect(screen.getByText(/China/)).toBeInTheDocument();
    // The attribution caption is always shown.
    expect(screen.getByText(/DB-IP/)).toBeInTheDocument();
  });

  it("maps the PRIVATE / UNKNOWN sentinels to their i18n labels", async () => {
    server.use(
      http.get("/api/tenants/t1/attacker-countries", () =>
        HttpResponse.json([
          { code: "PRIVATE", count: 5, pct: 50 },
          { code: "UNKNOWN", count: 5, pct: 50 },
        ]),
      ),
    );
    renderWithProviders(withTenant(<AttackerCountriesCard />));
    expect(await screen.findByText(/Private \/ internal/i)).toBeInTheDocument();
    expect(screen.getByText(/^Unknown$/i)).toBeInTheDocument();
  });

  it("shows the empty state when the API returns no rows", async () => {
    server.use(
      http.get("/api/tenants/t1/attacker-countries", () => HttpResponse.json([])),
    );
    renderWithProviders(withTenant(<AttackerCountriesCard />));
    expect(
      await screen.findByText(/no attacks recorded in this period/i),
    ).toBeInTheDocument();
  });
});
