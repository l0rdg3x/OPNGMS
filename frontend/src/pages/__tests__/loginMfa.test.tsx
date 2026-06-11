import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { LoginPage } from "../LoginPage";
import { AuthContext } from "../../auth/AuthProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const LOGIN_URL = "/api/login";
const LOGIN_MFA_URL = "/api/login/mfa";

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
    expect(mfaBody).toEqual({ code: "123456" });
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
    expect(mfaBody).toEqual({ code: "ABCDE-FGHIJ" });
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
});
