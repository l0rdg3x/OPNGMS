import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SystemSettingsPage } from "../SystemSettingsPage";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const LP = "http://localhost:3000/api/admin/live-push";

describe("SystemSettingsPage", () => {
  it("shows the live-push switch and toggles it", async () => {
    let enabled = false;
    server.use(
      http.get(LP, () => HttpResponse.json({ enabled })),
      http.put(LP, async ({ request }) => {
        enabled = (await request.json() as { enabled: boolean }).enabled;
        return HttpResponse.json({ enabled });
      }),
    );
    renderWithProviders(<SystemSettingsPage />);
    const sw = await screen.findByTestId("live-push-switch");
    expect(sw).not.toBeChecked();
    await userEvent.click(sw);
    await waitFor(() => expect(enabled).toBe(true));
  });
});
