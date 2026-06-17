import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MfaPanel } from "../MfaPanel";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

// Stub the browser WebAuthn plumbing: createCredential just echoes a serialised
// credential; webauthnSupported is forced true so the Add button is enabled.
vi.mock("../webauthnClient", () => ({
  webauthnSupported: () => true,
  createCredential: vi.fn(async () => ({
    id: "cred-abc",
    rawId: "cred-abc",
    type: "public-key",
    response: { attestationObject: "AA", clientDataJSON: "BB", transports: ["internal"] },
  })),
}));

// localStorage is not guaranteed in the vitest pool; stub a minimal in-memory one.
const localStorageStub = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
  };
})();
vi.stubGlobal("localStorage", localStorageStub);

beforeEach(() => localStorageStub.clear());

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
const PK_BEGIN_URL = "/api/me/mfa/webauthn/register/begin";
const PK_COMPLETE_URL = "/api/me/mfa/webauthn/register/complete";
const PK_LIST_URL = "/api/me/mfa/webauthn/credentials";

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
      http.get(TRUSTED_DEVICE_TOGGLE_URL, () => HttpResponse.json({ enabled: false })),
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

  it("superadmin sees the trusted-device toggle (enabled) and can turn it off", async () => {
    let putBody: unknown = null;
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({ enabled: false, recovery_codes_remaining: 0 }),
      ),
      http.get(POLICY_URL, () => HttpResponse.json({ mode: "off" })),
      http.put(POLICY_URL, async ({ request }) => HttpResponse.json({ mode: (await request.json() as { mode: string }).mode })),
      http.get(USERS_URL, () => HttpResponse.json([])),
      http.get(TRUSTED_DEVICE_TOGGLE_URL, () => HttpResponse.json({ enabled: true })),
      http.put(TRUSTED_DEVICE_TOGGLE_URL, async ({ request }) => {
        putBody = await request.json();
        return HttpResponse.json({ enabled: (putBody as { enabled: boolean }).enabled });
      }),
    );

    renderWithProviders(withAuth(<MfaPanel />, true));

    // Switch should be checked (enabled=true) — wait for the query to settle
    const toggle = await screen.findByTestId("trusted-device-toggle");
    await waitFor(() => expect(toggle).toBeChecked());
    await waitFor(() => expect(toggle).not.toBeDisabled());

    // Click it to turn off
    await userEvent.click(toggle);

    await waitFor(() => expect(putBody).toEqual({ enabled: false }));
  });
});

describe("MfaPanel — passkeys (WebAuthn)", () => {
  it("hides the passkeys section when WebAuthn is not configured", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: false,
          recovery_codes_remaining: 0,
          webauthn: { configured: false, credentials: 0 },
        }),
      ),
    );
    renderWithProviders(withAuth(<MfaPanel />));
    await screen.findByTestId("mfa-enroll");
    expect(screen.queryByTestId("mfa-passkeys")).not.toBeInTheDocument();
  });

  it("shows the passkeys section + the registered list when configured", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: true,
          recovery_codes_remaining: 5,
          webauthn: { configured: true, credentials: 1 },
        }),
      ),
      http.get(PK_LIST_URL, () =>
        HttpResponse.json([
          { id: "pk1", name: "Work laptop", created_at: "2026-01-02T10:00:00Z", last_used_at: null },
        ]),
      ),
    );
    renderWithProviders(withAuth(<MfaPanel />));
    await screen.findByTestId("mfa-passkeys");
    const row = await screen.findByTestId("mfa-passkey-row-pk1");
    expect(within(row).getByText("Work laptop")).toBeInTheDocument();
  });

  it("adds a passkey: begin(with password) → complete → list refreshes", async () => {
    let beginBody: unknown = null;
    let completeBody: unknown = null;
    let listCalls = 0;
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: true,
          recovery_codes_remaining: 5,
          webauthn: { configured: true, credentials: 0 },
        }),
      ),
      http.get(PK_LIST_URL, () => {
        listCalls += 1;
        // empty before adding, one item after the invalidation re-fetch
        return HttpResponse.json(
          listCalls > 1
            ? [{ id: "pk1", name: "Yubi", created_at: "2026-01-02T10:00:00Z", last_used_at: null }]
            : [],
        );
      }),
      http.post(PK_BEGIN_URL, async ({ request }) => {
        beginBody = await request.json();
        return HttpResponse.json({
          challenge: "Y2hhbGxlbmdl",
          rp: { id: "example.com", name: "OPNGMS" },
          user: { id: "dXNlcg", name: "u@x.io", displayName: "User" },
          pubKeyCredParams: [{ type: "public-key", alg: -7 }],
        });
      }),
      http.post(PK_COMPLETE_URL, async ({ request }) => {
        completeBody = await request.json();
        return HttpResponse.json({
          id: "pk1", name: "Yubi", created_at: "2026-01-02T10:00:00Z", last_used_at: null,
        });
      }),
    );

    renderWithProviders(withAuth(<MfaPanel />));
    await screen.findByTestId("mfa-passkeys");

    await userEvent.type(screen.getByTestId("mfa-passkey-password"), "pw12345");
    await userEvent.type(screen.getByTestId("mfa-passkey-name"), "Yubi");
    await userEvent.click(screen.getByTestId("mfa-passkey-add"));

    await waitFor(() => expect(beginBody).toEqual({ password: "pw12345" }));
    await waitFor(() => expect((completeBody as { name?: string }).name).toBe("Yubi"));
    // The new credential appears after the list invalidation.
    await screen.findByTestId("mfa-passkey-row-pk1");
  });

  it("surfaces the last-factor guard (409) when removing a passkey", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: false,
          recovery_codes_remaining: 0,
          webauthn: { configured: true, credentials: 1 },
        }),
      ),
      http.get(PK_LIST_URL, () =>
        HttpResponse.json([
          { id: "pk1", name: "Only key", created_at: "2026-01-02T10:00:00Z", last_used_at: null },
        ]),
      ),
      http.delete("/api/me/mfa/webauthn/credentials/pk1", () =>
        HttpResponse.json({ detail: "last factor" }, { status: 409 }),
      ),
    );

    renderWithProviders(withAuth(<MfaPanel />));
    await screen.findByTestId("mfa-passkey-row-pk1");

    await userEvent.click(screen.getByTestId("mfa-passkey-remove-pk1"));
    await userEvent.click(await screen.findByTestId("confirm-ok"));

    expect(await screen.findByRole("alert")).toHaveTextContent(/last second factor/i);
  });
});

const TRUSTED_DEVICE_TOGGLE_URL = "/api/admin/trusted-device-enabled";
const TRUSTED_DEVICES_URL = "/api/me/trusted-devices";

describe("MfaPanel — trusted devices", () => {
  it("shows trusted devices section when enabled", async () => {
    let listCalls = 0;
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: true,
          recovery_codes_remaining: 3,
          trusted_devices: { enabled: true },
        }),
      ),
      http.get(TRUSTED_DEVICES_URL, () => {
        listCalls += 1;
        // empty after the invalidation re-fetch that follows revoke
        return HttpResponse.json(
          listCalls > 1
            ? []
            : [
                {
                  id: "d1",
                  user_agent: "Chrome on Linux",
                  ip: "1.2.3.4",
                  created_at: "2026-01-01T00:00:00Z",
                  last_used_at: "2026-01-10T12:00:00Z",
                  expires_at: "2026-02-01T00:00:00Z",
                },
              ],
        );
      }),
      http.delete(`${TRUSTED_DEVICES_URL}/d1`, () => new HttpResponse(null, { status: 204 })),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    // Section title is visible
    expect(await screen.findByText("Trusted devices")).toBeInTheDocument();

    // Device row contains user agent and IP
    expect(await screen.findByText("Chrome on Linux")).toBeInTheDocument();
    expect(await screen.findByText("1.2.3.4")).toBeInTheDocument();

    // Click revoke button for d1
    await userEvent.click(screen.getByTestId("trusted-device-revoke-d1"));

    // Confirm in the modal
    await userEvent.click(await screen.findByTestId("confirm-ok"));

    // After revoke the empty state appears
    expect(await screen.findByText("No trusted devices.")).toBeInTheDocument();
  });

  it("revoke-all requires confirmation before calling DELETE (all)", async () => {
    const revokeAllCalled = vi.fn();
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: true,
          recovery_codes_remaining: 3,
          trusted_devices: { enabled: true },
        }),
      ),
      http.get(TRUSTED_DEVICES_URL, () =>
        HttpResponse.json([
          {
            id: "d2",
            user_agent: "Firefox on Mac",
            ip: "5.6.7.8",
            created_at: "2026-01-01T00:00:00Z",
            last_used_at: "2026-01-10T12:00:00Z",
            expires_at: "2026-02-01T00:00:00Z",
          },
        ]),
      ),
      http.delete(TRUSTED_DEVICES_URL, () => {
        revokeAllCalled();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    // Wait for the device to appear
    await screen.findByText("Firefox on Mac");

    // Click "Revoke all" — should NOT fire immediately
    await userEvent.click(screen.getByTestId("trusted-device-revoke-all"));

    // The DELETE must NOT have been called yet
    expect(revokeAllCalled).not.toHaveBeenCalled();

    // Confirm in the modal
    await userEvent.click(await screen.findByTestId("confirm-ok"));

    await waitFor(() => expect(revokeAllCalled).toHaveBeenCalled());
  });

  it("hides trusted devices section when disabled", async () => {
    server.use(
      http.get(STATUS_URL, () =>
        HttpResponse.json({
          enabled: true,
          recovery_codes_remaining: 3,
          trusted_devices: { enabled: false },
        }),
      ),
    );

    renderWithProviders(withAuth(<MfaPanel />));

    // Wait for MFA status to load (badge appears)
    await screen.findByTestId("mfa-status-badge");

    // Trusted devices section title must NOT be in the DOM
    expect(screen.queryByText("Trusted devices")).not.toBeInTheDocument();
  });
});
