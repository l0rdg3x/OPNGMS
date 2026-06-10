import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { SessionsPage } from "./SessionsPage";
import { server } from "../test/server";
import { renderWithProviders } from "../test/utils";

const SESSIONS_URL = "/api/sessions";
const LOGOUT_ALL_URL = "/api/logout-all";

const twoSessions = [
  {
    id: "sess-1",
    created_at: "2026-06-01T10:00:00Z",
    last_seen_at: "2026-06-10T12:00:00Z",
    expires_at: "2026-06-11T10:00:00Z",
    ip: "203.0.113.5",
    user_agent: "Mozilla/5.0",
    current: true,
  },
  {
    id: "sess-2",
    created_at: "2026-06-05T08:00:00Z",
    last_seen_at: "2026-06-09T08:00:00Z",
    expires_at: "2026-06-06T08:00:00Z",
    ip: "203.0.113.9",
    user_agent: "curl/7.88",
    current: false,
  },
];

describe("SessionsPage", () => {
  it("renders both session rows and badges the current one", async () => {
    server.use(http.get(SESSIONS_URL, () => HttpResponse.json(twoSessions)));

    renderWithProviders(<SessionsPage />);

    // Both rows appear (identified by IP)
    expect(await screen.findByText("203.0.113.5")).toBeInTheDocument();
    expect(await screen.findByText("203.0.113.9")).toBeInTheDocument();

    // Only the current session has the badge
    expect(screen.getByTestId("badge-current")).toBeInTheDocument();
  });

  it("clicking Log out everywhere calls POST /api/logout-all", async () => {
    const logoutAllCalled = vi.fn();

    server.use(
      http.get(SESSIONS_URL, () => HttpResponse.json(twoSessions)),
      http.post(LOGOUT_ALL_URL, () => {
        logoutAllCalled();
        return new HttpResponse(null, { status: 204 });
      }),
    );

    renderWithProviders(<SessionsPage />);

    // Wait for the table to load
    await screen.findByText("203.0.113.5");

    await userEvent.click(screen.getByTestId("btn-logout-all"));

    await waitFor(() => expect(logoutAllCalled).toHaveBeenCalled());
  });
});
