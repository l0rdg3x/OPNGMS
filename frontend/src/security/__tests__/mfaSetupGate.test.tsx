import { screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ProtectedRoute } from "../../auth/ProtectedRoute";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { http, HttpResponse } from "msw";
import { renderWithProviders } from "../../test/utils";

function withAuth(node: ReactNode, mfa_setup_required: boolean, refresh = vi.fn()) {
  return (
    <AuthContext.Provider
      value={{
        me: { id: "u1", email: "u@x.io", name: "User", is_superadmin: false, mfa_setup_required },
        loading: false,
        refresh,
        setMe: vi.fn(),
      }}
    >
      {node}
    </AuthContext.Provider>
  );
}

describe("ProtectedRoute — forced MFA setup gate", () => {
  it("renders children when mfa_setup_required is false", () => {
    renderWithProviders(
      withAuth(<ProtectedRoute><div>App content</div></ProtectedRoute>, false),
    );
    expect(screen.getByText("App content")).toBeInTheDocument();
  });

  it("replaces app content with the gate when mfa_setup_required is true", async () => {
    server.use(
      http.get("/api/me/mfa", () =>
        HttpResponse.json({ enabled: false, recovery_codes_remaining: 0 }),
      ),
    );
    renderWithProviders(
      withAuth(<ProtectedRoute><div>App content</div></ProtectedRoute>, true),
    );
    // App content is gone, the gate enroll flow is shown
    expect(screen.queryByText("App content")).not.toBeInTheDocument();
    expect(await screen.findByTestId("mfa-enroll")).toBeInTheDocument();
  });
});
