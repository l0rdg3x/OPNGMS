import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SystemSettingsPage } from "../SystemSettingsPage";
import { RuntimeSettingsSection } from "../../admin/RuntimeSettingsSection";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

const LP = "http://localhost:3000/api/admin/live-push";
const RS = "http://localhost:3000/api/admin/settings";

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

describe("RuntimeSettingsSection", () => {
  const initial = {
    settings: [
      { key: "silent_alert_enabled", value: true, default: true, kind: "bool", minimum: null, maximum: null, group: "maintenance" },
      { key: "session_ttl_hours", value: 12, default: 12, kind: "int", minimum: 1, maximum: 8760, group: "security_session" },
    ],
  };

  it("toggles a boolean setting and saves only the changed value", async () => {
    let saved: { values: Record<string, unknown> } | null = null;
    server.use(
      http.get(RS, () => HttpResponse.json(initial)),
      http.put(RS, async ({ request }) => {
        saved = (await request.json()) as { values: Record<string, unknown> };
        return HttpResponse.json({
          settings: initial.settings.map((s) =>
            s.key === "silent_alert_enabled" ? { ...s, value: false } : s,
          ),
        });
      }),
    );
    renderWithProviders(<RuntimeSettingsSection />);

    const sw = await screen.findByTestId("rs-silent_alert_enabled");
    expect(sw).toBeChecked();
    // Save is disabled until something is edited.
    expect(screen.getByTestId("runtime-settings-save")).toBeDisabled();

    await userEvent.click(sw);
    await userEvent.click(screen.getByTestId("runtime-settings-save"));

    await waitFor(() => expect(saved).toEqual({ values: { silent_alert_enabled: false } }));
  });
});
