import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { LoginPage } from "../LoginPage";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

// Stub the browser WebAuthn plumbing: getAssertion echoes a serialised assertion;
// webauthnSupported is forced true so the passkey button is usable.
vi.mock("../../security/webauthnClient", () => ({
  webauthnSupported: () => true,
  getAssertion: vi.fn(async () => ({
    id: "cred-abc",
    rawId: "cred-abc",
    type: "public-key",
    response: { authenticatorData: "AA", clientDataJSON: "BB", signature: "CC", userHandle: null },
  })),
}));

// localStorage may be absent in the vitest pool; provide a minimal in-memory stub.
vi.stubGlobal("localStorage", (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => { store[k] = v; },
    removeItem: (k: string) => { delete store[k]; },
    clear: () => { store = {}; },
  };
})());

const LOGIN_URL = "/api/login";
const LOGIN_MFA_URL = "/api/login/mfa";
const WA_BEGIN_URL = "/api/login/webauthn/begin";
const WA_COMPLETE_URL = "/api/login/webauthn/complete";

function withAuth(node: ReactNode, setMe = vi.fn()) {
  return (
    <AuthContext.Provider
      value={{ me: null, loading: false, refresh: vi.fn(), setMe }}
    >
      {node}
    </AuthContext.Provider>
  );
}

const fullUser = {
  id: "u1",
  email: "u@x.io",
  name: "User",
  is_superadmin: false,
  mfa_setup_required: false,
};

async function fillPasswordStep() {
  await userEvent.type(screen.getByLabelText(/Email/), "u@x.io");
  await userEvent.type(screen.getByLabelText(/Password/), "pw12345");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginPage — MFA", () => {
  it("password step with no MFA logs in directly (status ok)", async () => {
    const setMe = vi.fn();
    server.use(
      http.post(LOGIN_URL, () => HttpResponse.json({ status: "ok", user: fullUser })),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    await waitFor(() => expect(setMe).toHaveBeenCalledWith(fullUser));
  });

  it("mfa_required → enter TOTP code → POST /api/login/mfa → setMe on ok", async () => {
    const setMe = vi.fn();
    let mfaBody: unknown = null;
    server.use(
      http.post(LOGIN_URL, () => HttpResponse.json({ status: "mfa_required" })),
      http.post(LOGIN_MFA_URL, async ({ request }) => {
        mfaBody = await request.json();
        return HttpResponse.json({ status: "ok", user: fullUser });
      }),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    // The code step appears
    const codeInput = await screen.findByTestId("mfa-code");
    await userEvent.type(codeInput, "123456");
    await userEvent.click(screen.getByTestId("mfa-verify"));

    await waitFor(() => expect(setMe).toHaveBeenCalledWith(fullUser));
    expect(mfaBody).toEqual({ code: "123456", remember_device: false });
  });

  it("mfa_required → recovery-code toggle posts the recovery code", async () => {
    const setMe = vi.fn();
    let mfaBody: unknown = null;
    server.use(
      http.post(LOGIN_URL, () => HttpResponse.json({ status: "mfa_required" })),
      http.post(LOGIN_MFA_URL, async ({ request }) => {
        mfaBody = await request.json();
        return HttpResponse.json({ status: "ok", user: fullUser });
      }),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    await screen.findByTestId("mfa-code");
    // Switch to recovery-code mode
    await userEvent.click(screen.getByTestId("mfa-use-recovery"));

    const codeInput = screen.getByTestId("mfa-code");
    await userEvent.type(codeInput, "ABCDE-FGHIJ");
    await userEvent.click(screen.getByTestId("mfa-verify"));

    await waitFor(() => expect(setMe).toHaveBeenCalledWith(fullUser));
    expect(mfaBody).toEqual({ code: "ABCDE-FGHIJ", remember_device: false });
  });

  it("mfa_required → wrong code shows an inline error and does not call setMe", async () => {
    const setMe = vi.fn();
    server.use(
      http.post(LOGIN_URL, () => HttpResponse.json({ status: "mfa_required" })),
      http.post(LOGIN_MFA_URL, () => HttpResponse.json({ detail: "Invalid code" }, { status: 401 })),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    await screen.findByTestId("mfa-code");
    await userEvent.type(screen.getByTestId("mfa-code"), "000000");
    await userEvent.click(screen.getByTestId("mfa-verify"));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(setMe).not.toHaveBeenCalled();
  });

  it("mfa_setup_required → setMe with the user (gate handles the rest)", async () => {
    const setMe = vi.fn();
    const setupUser = { ...fullUser, mfa_setup_required: true };
    server.use(
      http.post(LOGIN_URL, () =>
        HttpResponse.json({ status: "mfa_setup_required", user: setupUser }),
      ),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    await waitFor(() => expect(setMe).toHaveBeenCalledWith(setupUser));
  });

  it("methods=[totp] only → no passkey button is shown", async () => {
    server.use(
      http.post(LOGIN_URL, () =>
        HttpResponse.json({ status: "mfa_required", methods: ["totp"] }),
      ),
    );
    renderWithProviders(withAuth(<LoginPage />));
    await fillPasswordStep();

    await screen.findByTestId("mfa-code");
    expect(screen.queryByTestId("mfa-use-passkey")).not.toBeInTheDocument();
  });

  it("methods include webauthn → passkey button begins, asserts, completes, logs in", async () => {
    const setMe = vi.fn();
    let completeBody: unknown = null;
    server.use(
      http.post(LOGIN_URL, () =>
        HttpResponse.json({ status: "mfa_required", methods: ["totp", "webauthn"] }),
      ),
      http.post(WA_BEGIN_URL, () =>
        HttpResponse.json({
          challenge: "Y2hhbGxlbmdl",
          rpId: "example.com",
          allowCredentials: [{ type: "public-key", id: "Y3JlZA" }],
        }),
      ),
      http.post(WA_COMPLETE_URL, async ({ request }) => {
        completeBody = await request.json();
        return HttpResponse.json({ status: "ok", user: fullUser });
      }),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    // Both the TOTP field and the passkey button are present.
    await screen.findByTestId("mfa-code");
    await userEvent.click(await screen.findByTestId("mfa-use-passkey"));

    await waitFor(() => expect(setMe).toHaveBeenCalledWith(fullUser));
    expect((completeBody as { credential?: unknown }).credential).toBeTruthy();
  });

  it("remember_device enabled → checkbox shown with days; checked value sent in body", async () => {
    const setMe = vi.fn();
    let mfaBody: unknown = null;
    server.use(
      http.post(LOGIN_URL, () =>
        HttpResponse.json({ status: "mfa_required", remember_device: { enabled: true, days: 30 } }),
      ),
      http.post(LOGIN_MFA_URL, async ({ request }) => {
        mfaBody = await request.json();
        return HttpResponse.json({ status: "ok", user: fullUser });
      }),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    // Checkbox must be visible and its label must contain the days number
    const checkbox = await screen.findByTestId("mfa-remember-device");
    expect(checkbox).toBeInTheDocument();
    // The surrounding container text contains "30"
    expect(document.body.textContent).toContain("30");

    // Click the checkbox to trust this device
    await userEvent.click(checkbox);

    // Now submit the TOTP code
    const codeInput = await screen.findByTestId("mfa-code");
    await userEvent.type(codeInput, "123456");
    await userEvent.click(screen.getByTestId("mfa-verify"));

    await waitFor(() => expect(setMe).toHaveBeenCalledWith(fullUser));
    expect(mfaBody).toEqual({ code: "123456", remember_device: true });
  });

  it("remember_device absent → checkbox not rendered", async () => {
    server.use(
      http.post(LOGIN_URL, () => HttpResponse.json({ status: "mfa_required" })),
      http.post(LOGIN_MFA_URL, () => HttpResponse.json({ status: "ok", user: fullUser })),
    );

    renderWithProviders(withAuth(<LoginPage />));
    await fillPasswordStep();

    await screen.findByTestId("mfa-code");
    expect(screen.queryByTestId("mfa-remember-device")).toBeNull();
  });

  it("webauthn-only account → passkey button is the sole second factor", async () => {
    const setMe = vi.fn();
    server.use(
      http.post(LOGIN_URL, () =>
        HttpResponse.json({ status: "mfa_required", methods: ["webauthn"] }),
      ),
      http.post(WA_BEGIN_URL, () =>
        HttpResponse.json({ challenge: "Y2hhbGxlbmdl", allowCredentials: [] }),
      ),
      http.post(WA_COMPLETE_URL, () => HttpResponse.json({ status: "ok", user: fullUser })),
    );

    renderWithProviders(withAuth(<LoginPage />, setMe));
    await fillPasswordStep();

    await screen.findByTestId("mfa-use-passkey");
    expect(screen.queryByTestId("mfa-code")).not.toBeInTheDocument();

    await userEvent.click(screen.getByTestId("mfa-use-passkey"));
    await waitFor(() => expect(setMe).toHaveBeenCalledWith(fullUser));
  });
});
