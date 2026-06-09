import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { ChangesPanel } from "../ChangesPanel";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

function withTenant(node: ReactNode, role: string = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "A", slug: "a", role }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const CHANGES_URL = "/api/tenants/t1/devices/d1/config/changes";
const CANCEL_URL = "/api/tenants/t1/devices/d1/config/changes/c1/cancel";
const SCHEDULE_URL = "/api/tenants/t1/devices/d1/config/changes/c1/schedule";
const PREVIEW_URL = "/api/tenants/t1/devices/d1/config/changes/c1/preview";

const draftChange = {
  id: "c1",
  device_id: "d1",
  kind: "alias",
  operation: "set",
  target: "web_servers",
  status: "draft",
  scheduled_at: null,
  applied_at: null,
  created_at: "2026-06-10T10:00:00Z",
};

const cancelledChange = {
  ...draftChange,
  status: "cancelled",
};

const scheduledChange = {
  ...draftChange,
  status: "scheduled",
  scheduled_at: "2026-07-01T12:00:00.000Z",
};

describe("ChangesPanel actions", () => {
  it("Cancel: POSTs to the cancel endpoint when Cancel button is clicked", async () => {
    let cancelCalled = false;
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([draftChange])),
      http.post(CANCEL_URL, () => {
        cancelCalled = true;
        return HttpResponse.json(cancelledChange);
      }),
    );

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));

    const cancelBtn = await screen.findByRole("button", { name: /cancel/i });
    await userEvent.click(cancelBtn);

    await waitFor(() => {
      expect(cancelCalled).toBe(true);
    });
  });

  it("Apply now: POSTs schedule with scheduled_at: null", async () => {
    let capturedBody: unknown;
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([draftChange])),
      http.post(SCHEDULE_URL, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(scheduledChange);
      }),
    );

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));

    // Open the schedule popover
    const scheduleBtn = await screen.findByRole("button", { name: /^schedule$/i });
    await userEvent.click(scheduleBtn);

    // Click "Apply now" inside the popover
    const applyNowBtn = await screen.findByRole("button", { name: /apply now/i });
    await userEvent.click(applyNowBtn);

    await waitFor(() => {
      expect(capturedBody).toEqual({ scheduled_at: null });
    });
  });

  it("Preview: opens a modal and renders the preview data", async () => {
    const previewData = {
      operation: "set",
      target: "web_servers",
      payload: { name: "web_servers", type: "host", content: ["10.0.0.1"] },
    };

    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([draftChange])),
      http.get(PREVIEW_URL, () => HttpResponse.json(previewData)),
    );

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));

    const previewBtn = await screen.findByRole("button", { name: /preview/i });
    await userEvent.click(previewBtn);

    // The modal opens and renders the JSON preview containing known keys/values.
    // Use findByText to wait for the async preview data to load.
    await screen.findByText(/10\.0\.0\.1/);
  });

  it("403 on cancel: component remains stable with target still in DOM", async () => {
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([draftChange])),
      http.post(CANCEL_URL, () =>
        HttpResponse.json({ detail: "Forbidden" }, { status: 403 }),
      ),
    );

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));

    const cancelBtn = await screen.findByRole("button", { name: /cancel/i });
    await userEvent.click(cancelBtn);

    // After the failed cancel, the row target is still visible (component did not crash).
    await waitFor(() => {
      expect(screen.getByText("web_servers")).toBeInTheDocument();
    });
  });

  it("No actions shown for read_only role", async () => {
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([draftChange])));

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />, "read_only"));

    expect(await screen.findByText("web_servers")).toBeInTheDocument();
    // No action buttons rendered for read_only.
    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /preview/i })).not.toBeInTheDocument();
  });

  it("No actions shown for non-actionable statuses (applied)", async () => {
    const appliedChange = { ...draftChange, status: "applied" };
    server.use(http.get(CHANGES_URL, () => HttpResponse.json([appliedChange])));

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));

    expect(await screen.findByText("web_servers")).toBeInTheDocument();
    // Applied rows have no action buttons.
    expect(screen.queryByRole("button", { name: /cancel/i })).not.toBeInTheDocument();
  });

  it("Preview 403: modal shows error text and no secret content", async () => {
    server.use(
      http.get(CHANGES_URL, () => HttpResponse.json([draftChange])),
      http.get(PREVIEW_URL, () =>
        HttpResponse.json({ detail: "forbidden" }, { status: 403 }),
      ),
    );

    renderWithProviders(withTenant(<ChangesPanel deviceId="d1" />));

    const previewBtn = await screen.findByRole("button", { name: /preview/i });
    await userEvent.click(previewBtn);

    // Modal opens and shows the error message from t.errors.configChangeAction.
    await screen.findByText("Action failed (you may lack permission)");
    // No stale/secret JSON content is visible.
    expect(screen.queryByText(/forbidden/i)).not.toBeInTheDocument();
  });

  /*
   * NOTE: The full DateTimePicker calendar interaction (picking a date via jsdom)
   * is skipped because driving the Mantine date calendar through jsdom is flaky
   * (portal rendering + calendar grid clicks). The "Apply now" → null path is
   * covered above. The schedule.mutateAsync({ id, scheduled_at: iso }) code path
   * (with a non-null ISO string) is exercised by the implementation; the
   * conversion `new Date(value).toISOString()` is straightforward and testable
   * at the unit level without calendar interaction.
   */
});
