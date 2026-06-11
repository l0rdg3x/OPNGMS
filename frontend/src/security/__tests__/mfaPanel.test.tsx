import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { MfaPanel } from "../MfaPanel";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withAuth(node: ReactNode, is_superadmin = false) {
  return (
    <AuthContext.Provider
      value={{
        me: { id: "u1", email: "u@x.io", name: "User", is_superadmin },
        loading: false,
        refresh: vi.fn(),
        setMe: vi.fn(),
      }}
    >
      {node}
    </AuthContext.Provider>
  );
}

const STATUS_URL = "/api/me/mfa";
const SETUP_URL = "/api/me/mfa/setup";
const CONFIRM_URL = "/api/me/mfa/confirm";
const DISABLE_URL = "/api/me/mfa/disable";
const REGEN_URL = "/api/me/mfa/recovery/regenerate";
const POLICY_URL = "/api/admin/mfa-policy";
const USERS_URL = "/api/users";

const recoveryCodes = [
  "AAAAA-11111", "BBBBB-22222", "CCCCC-33333", "DDDDD-44444", "EEEEE-55555",
  "FFFFF-66666", "GGGGG-77777", "HHHHH-88888", "JJJJJ-99999", "KKKKK-00000",
];

describe("MfaPanel — enrollment", () => {
  it("disabled status → password → setup → QR + secret → confirm → recovery codes shown once", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: false, recovery_codes_remaining: 0 }),
      ),
      http.post(SETUP_URL, () =>
        HttpResponse.json({ otpauth_uri: "otpauth://totp/OPNGMS:u@x.io?secret=JBSWY3DPEHPK3PXP&issuer=OPNGMS", secret: "JBSWY3DPEHPK3PXP" }),
      ),
      http.post(CONFIRM_URL, () => HttpResponse.json({ recovery_codes: recoveryCodes })),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    // Status shows disabled, enroll available
    await screen.findByTestId("mfa-enroll");

    // Enter password and start setup
    await userEvent.type(screen.getByTestId("mfa-enroll-password"), "pw12345");
    await userEvent.click(screen.getByTestId("mfa-enroll"));

    // Secret is shown
    const secret = await screen.findByTestId("mfa-secret");
    expect(secret).toHaveTextContent("JBSWY3DPEHPK3PXP");

    // Confirm code → recovery codes
    await userEvent.type(screen.getByTestId("mfa-confirm-code"), "123456");
    await userEvent.click(screen.getByTestId("mfa-confirm"));

    const codesBlock = await screen.findByTestId("mfa-recovery-codes");
    expect(within(codesBlock).getByText("AAAAA-11111")).toBeInTheDocument();
    expect(within(codesBlock).getByText("KKKKK-00000")).toBeInTheDocument();
  });

  it("setup with a wrong password shows an error and does not advance", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: false, recovery_codes_remaining: 0 }),
      ),
      http.post(SETUP_URL, () => HttpResponse.json({ detail: "Password required" }, { status: 403 })),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    await userEvent.type(await screen.findByTestId("mfa-enroll-password"), "WRONG");
    await userEvent.click(screen.getByTestId("mfa-enroll"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByTestId("mfa-secret")).not.toBeInTheDocument();
  });
});

describe("MfaPanel — enabled management", () => {
  it("shows enabled status + remaining recovery count", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: true, recovery_codes_remaining: 7 }),
      ),
    );
    renderWithProviders(withAuth(<MfaPanel />));
    expect(await screen.findByText("7")).toBeInTheDocument();
    expect(screen.getByTestId("mfa-disable")).toBeInTheDocument();
  });

  it("disable requires a password and POSTs to disable", async () => {
    const disableBody = vi.fn();
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: true, recovery_codes_remaining: 7 }),
      ),
      http.post(DISABLE_URL, async ({ request }) => {
        disableBody(await request.json());
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    await userEvent.type(await screen.findByTestId("mfa-disable-password"), "pw12345");
    await userEvent.click(screen.getByTestId("mfa-disable"));
    // Disable is gated behind a confirmation modal.
    await userEvent.click(await screen.findByTestId("confirm-ok"));

    await waitFor(() => expect(disableBody).toHaveBeenCalledWith({ password: "pw12345" }));
  });

  it("regenerate codes shows the new set", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: true, recovery_codes_remaining: 0 }),
      ),
      http.post(REGEN_URL, () => HttpResponse.json({ recovery_codes: recoveryCodes })),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    await userEvent.type(await screen.findByTestId("mfa-regen-password"), "pw12345");
    await userEvent.click(screen.getByTestId("mfa-regen"));

    const codesBlock = await screen.findByTestId("mfa-recovery-codes");
    expect(within(codesBlock).getByText("AAAAA-11111")).toBeInTheDocument();
  });
});

describe("MfaPanel — superadmin section", () => {
  it("non-superadmin does not see the policy/users section", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: false, recovery_codes_remaining: 0 }),
      ),
    );
    renderWithProviders(withAuth(<MfaPanel />, false));
    await screen.findByTestId("mfa-enroll");
    expect(screen.queryByTestId("mfa-policy")).not.toBeInTheDocument();
  });

  it("superadmin can change the policy (PUT) and reset a user's MFA (POST)", async () => {
    let policyBody: unknown = null;
    const resetCalled = vi.fn();
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: false, recovery_codes_remaining: 0 }),
      ),
      http.get(POLICY_URL, () => HttpResponse.json({ mode: "off" })),
      http.put(POLICY_URL, async ({ request }) => {
        policyBody = await request.json();
        return HttpResponse.json({ mode: (policyBody as { mode: string }).mode });
      }),
      http.get(USERS_URL, () =>
        HttpResponse.json([
          { id: "u1", email: "u@x.io", name: "User", is_superadmin: true, status: "active" },
          { id: "u2", email: "v@x.io", name: "Vera", is_superadmin: false, status: "active" },
        ]),
      ),
      http.post("/api/users/u2/mfa/reset", () => {
        resetCalled();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(withAuth(<MfaPanel />, true));

    // Change policy via the SegmentedControl (click "All users")
    const policy = await screen.findByTestId("mfa-policy");
    await userEvent.click(within(policy).getByText("All users"));
    await waitFor(() => expect(policyBody).toEqual({ mode: "all" }));

    // Reset Vera's MFA → confirm modal → confirm
    const row = await screen.findByTestId("mfa-user-row-u2");
    await userEvent.click(within(row).getByTestId("mfa-reset-u2"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));

    await waitFor(() => expect(resetCalled).toHaveBeenCalled());
  });
});
