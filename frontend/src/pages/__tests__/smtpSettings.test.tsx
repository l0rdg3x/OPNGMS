import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SmtpSettingsPage } from "../SmtpSettingsPage";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withAuth(node: ReactNode, is_superadmin = false) {
  return (
    <AuthContext.Provider value={{
      me: { id: "u1", email: "u@x.io", name: "User", is_superadmin },
      loading: false, refresh: vi.fn(), setMe: vi.fn(),
    }}>{node}</AuthContext.Provider>
  );
}

const SMTP = "/api/admin/smtp";

describe("SmtpSettingsPage", () => {
  it("loads, saves config (PUT) and runs a test send", async () => {
    let putBody: unknown = null;
    server.use(
      http.get(SMTP, () => HttpResponse.json({
        enabled: false, host: "", port: 587, security: "starttls", username: null,
        from_email: "", from_name: "", has_password: false })),
      http.put(SMTP, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ ...(putBody as object), has_password: true });
      }),
      http.post(`${SMTP}/test`, () => HttpResponse.json({ ok: true, detail: "sent" })),
    );

    renderWithProviders(withAuth(<SmtpSettingsPage />, true));
    await userEvent.type(await screen.findByTestId("smtp-host"), "smtp.x.io");
    await userEvent.type(screen.getByTestId("smtp-from-email"), "noc@x.io");
    await userEvent.click(screen.getByTestId("smtp-save"));
    await waitFor(() => expect((putBody as { host: string }).host).toBe("smtp.x.io"));
  });

  it("blocks non-superadmin", () => {
    renderWithProviders(withAuth(<SmtpSettingsPage />, false));
    expect(screen.getByTestId("smtp-forbidden")).toBeInTheDocument();
  });

  it("reveals OAuth fields and the Tenant ID only for Microsoft", async () => {
    server.use(
      http.get(SMTP, () => HttpResponse.json({
        enabled: false, host: "", port: 587, security: "starttls", username: null,
        from_email: "", from_name: "", has_password: false, auth_method: "password",
        oauth_provider: null, oauth_client_id: null, oauth_tenant_id: null,
        has_client_secret: false, has_refresh_token: false })),
    );

    renderWithProviders(withAuth(<SmtpSettingsPage />, true));
    // Password mode by default: no OAuth fields, username/password visible.
    await screen.findByTestId("smtp-host");
    expect(screen.queryByTestId("smtp-oauth-provider")).not.toBeInTheDocument();
    expect(screen.getByTestId("smtp-username")).toBeInTheDocument();

    // Switch to OAuth2: provider + client id/secret/refresh appear, username/password go away.
    await userEvent.click(within(screen.getByTestId("smtp-auth-method")).getByText("OAuth2"));
    expect(screen.getByTestId("smtp-oauth-provider")).toBeInTheDocument();
    expect(screen.getByTestId("smtp-oauth-client-id")).toBeInTheDocument();
    expect(screen.getByTestId("smtp-oauth-client-secret")).toBeInTheDocument();
    expect(screen.getByTestId("smtp-oauth-refresh-token")).toBeInTheDocument();
    expect(screen.queryByTestId("smtp-username")).not.toBeInTheDocument();
    // Google (default) -> no Tenant ID.
    expect(screen.queryByTestId("smtp-oauth-tenant-id")).not.toBeInTheDocument();

    // Select Microsoft 365 -> Tenant ID appears.
    await userEvent.click(screen.getByTestId("smtp-oauth-provider"));
    await userEvent.click(await screen.findByText("Microsoft 365"));
    await waitFor(() =>
      expect(screen.getByTestId("smtp-oauth-tenant-id")).toBeInTheDocument());
  });

  it("PUTs the oauth fields when saving in OAuth mode", async () => {
    let putBody: Record<string, unknown> | null = null;
    server.use(
      http.get(SMTP, () => HttpResponse.json({
        enabled: false, host: "", port: 587, security: "starttls", username: null,
        from_email: "", from_name: "", has_password: false, auth_method: "password",
        oauth_provider: null, oauth_client_id: null, oauth_tenant_id: null,
        has_client_secret: false, has_refresh_token: false })),
      http.put(SMTP, async ({ request }) => {
        putBody = (await request.json()) as Record<string, unknown>;
        return HttpResponse.json({ ...(putBody as object), has_client_secret: true,
          has_refresh_token: true });
      }),
    );

    renderWithProviders(withAuth(<SmtpSettingsPage />, true));
    await userEvent.type(await screen.findByTestId("smtp-host"), "smtp.gmail.com");
    await userEvent.type(screen.getByTestId("smtp-from-email"), "me@x.com");
    await userEvent.click(within(screen.getByTestId("smtp-auth-method")).getByText("OAuth2"));
    await userEvent.type(screen.getByTestId("smtp-oauth-client-id"), "cid");
    await userEvent.type(screen.getByTestId("smtp-oauth-client-secret"), "secret");
    await userEvent.type(screen.getByTestId("smtp-oauth-refresh-token"), "refresh");
    await userEvent.click(screen.getByTestId("smtp-save"));
    await waitFor(() => expect(putBody).not.toBeNull());
    expect(putBody!.auth_method).toBe("oauth");
    expect(putBody!.oauth_provider).toBe("google");
    expect(putBody!.oauth_client_id).toBe("cid");
    expect(putBody!.oauth_client_secret).toBe("secret");
    expect(putBody!.oauth_refresh_token).toBe("refresh");
  });
});
