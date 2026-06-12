import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
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
});
