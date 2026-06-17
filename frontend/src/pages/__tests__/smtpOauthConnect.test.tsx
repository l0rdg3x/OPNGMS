import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SmtpSettingsPage } from "../SmtpSettingsPage";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withAuth(node: ReactNode) {
  return (
    <AuthContext.Provider value={{
      me: { id: "u1", email: "u@x.io", name: "User", is_superadmin: true },
      loading: false, refresh: vi.fn(), setMe: vi.fn(),
    }}>{node}</AuthContext.Provider>
  );
}

const SMTP = "/api/admin/smtp";
const AUTHORIZE_GOOGLE = "/api/admin/smtp/oauth/google/authorize";

/** Shared OAuth-mode GET /api/admin/smtp response (has_client_secret + oauth_client_id set). */
const OAUTH_SMTP_RESPONSE = {
  auth_method: "oauth",
  oauth_provider: "google",
  has_client_secret: true,
  oauth_client_id: "cid",
  enabled: true,
  host: "smtp.gmail.com",
  port: 587,
  security: "tls",
  username: "user@example.com",
  from_email: "user@example.com",
  from_name: "Test",
  has_password: false,
  oauth_tenant_id: null,
  has_refresh_token: false,
};

describe("SmtpSettingsPage — OAuth Connect button", () => {
  it("Test A: shows Connect with Google button + experimental badge, and starts the OAuth flow on click", async () => {
    // Track how many times the authorize endpoint is called.
    let authorizeCallCount = 0;
    // The authorize URL returned by the mock — used to verify the redirect.
    // jsdom does not permit redefining window.location.href (non-configurable),
    // so we verify the OAuth flow via the endpoint call count instead.
    const authorizeUrls: string[] = [];

    server.use(
      http.get(SMTP, () => HttpResponse.json(OAUTH_SMTP_RESPONSE)),
      http.get(AUTHORIZE_GOOGLE, () => {
        authorizeCallCount += 1;
        const url = "https://accounts.google.com/o/oauth2/v2/auth?x=1";
        authorizeUrls.push(url);
        return HttpResponse.json({ authorize_url: url });
      }),
    );

    renderWithProviders(withAuth(<SmtpSettingsPage />));

    // Wait for the page to load and the Connect button to appear.
    const connectBtn = await screen.findByTestId("smtp-oauth-connect");
    expect(connectBtn).toBeInTheDocument();

    // Experimental badge must be visible.
    expect(screen.getByText("Experimental — untested")).toBeInTheDocument();

    // Click the connect button — this calls GET /authorize, receives the URL,
    // and assigns it to window.location.href (redirect).
    await userEvent.click(connectBtn);

    // The authorize endpoint must have been called exactly once.
    await waitFor(() => expect(authorizeCallCount).toBe(1));

    // The authorize URL should contain the Google OAuth host.
    expect(authorizeUrls[0]).toContain("accounts.google.com");
  });

  it("Test B: shows hint text instead of Connect button when client secret or client ID is missing", async () => {
    server.use(
      http.get(SMTP, () => HttpResponse.json({
        ...OAUTH_SMTP_RESPONSE,
        has_client_secret: false,
        oauth_client_id: null,
      })),
    );

    renderWithProviders(withAuth(<SmtpSettingsPage />));

    // Wait for OAuth fields to load (oauth provider select is present in oauth mode).
    await screen.findByTestId("smtp-oauth-provider");

    // Connect button must NOT be present.
    expect(screen.queryByTestId("smtp-oauth-connect")).not.toBeInTheDocument();

    // Hint text must be shown instead.
    expect(screen.getByText("Save the client ID and secret first, then connect.")).toBeInTheDocument();
  });
});
