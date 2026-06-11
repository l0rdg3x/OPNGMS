import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import App from "../../App";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

describe("login flow", () => {
  it("shows login when unauthenticated, then the app after login", async () => {
    let authed = false;
    server.use(
      http.get("/api/me", () =>
        authed
          ? HttpResponse.json({ id: "1", email: "a@x.io", name: "A", is_superadmin: true })
          : new HttpResponse(null, { status: 401 }),
      ),
      http.post("/api/login", () => {
        authed = true;
        // New two-step login contract: { status, user }.
        return HttpResponse.json({
          status: "ok",
          user: { id: "1", email: "a@x.io", name: "A", is_superadmin: true },
        });
      }),
      http.get("/api/me/tenants", () => HttpResponse.json([])),
    );
    renderWithProviders(<App />);
    expect(await screen.findByLabelText(/email/i)).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText(/email/i), "a@x.io");
    await userEvent.type(screen.getByLabelText(/password/i), "pw12345");
    await userEvent.click(screen.getByRole("button", { name: /sign in/i }));
    await waitFor(() => expect(screen.getByText(/a@x.io/i)).toBeInTheDocument());
  });
});
