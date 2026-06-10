import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { notifications } from "@mantine/notifications";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ReportSettingsPage } from "../ReportSettingsPage";
import { TenantContext } from "../../tenant/TenantProvider";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

// ---------------------------------------------------------------------------
// Helper: wrap with a TenantContext that sets a specific role
// ---------------------------------------------------------------------------
function withTenant(node: ReactNode, role: string = "tenant_admin") {
  return (
    <TenantContext.Provider
      value={{
        tenants: [{ id: "t1", name: "Acme", slug: "acme", role }],
        activeId: "t1",
        setActiveId: () => {},
        loading: false,
      }}
    >
      {node}
    </TenantContext.Provider>
  );
}

const SETTINGS_URL = "http://localhost:3000/api/tenants/t1/reports/settings";
const LOGO_URL = "http://localhost:3000/api/tenants/t1/reports/settings/logo";

const defaultSettings = {
  title: "My Report",
  owner: "NOC",
  timezone: "UTC",
  has_logo: false,
  logo_mime: null,
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------
describe("ReportSettingsPage — tenant_admin", () => {
  it("renders the form populated from GET settings", async () => {
    server.use(http.get(SETTINGS_URL, () => HttpResponse.json(defaultSettings)));

    renderWithProviders(withTenant(<ReportSettingsPage />, "tenant_admin"));

    // Form fields appear with the loaded values
    const titleInput = await screen.findByTestId("field-title");
    expect(titleInput).toHaveValue("My Report");

    const ownerInput = screen.getByTestId("field-owner");
    expect(ownerInput).toHaveValue("NOC");

    const timezoneInput = screen.getByTestId("field-timezone");
    expect(timezoneInput).toHaveValue("UTC");

    // Save button is present
    expect(screen.getByTestId("btn-save")).toBeInTheDocument();

    // No logo uploaded — remove button should not be shown
    expect(screen.queryByTestId("btn-remove-logo")).not.toBeInTheDocument();
  });

  it("saving calls PUT with the edited values", async () => {
    const capturedBodies: unknown[] = [];

    server.use(
      http.get(SETTINGS_URL, () => HttpResponse.json(defaultSettings)),
      http.put(SETTINGS_URL, async ({ request }) => {
        capturedBodies.push(await request.json());
        return HttpResponse.json({ ...defaultSettings, title: "Updated" });
      }),
    );

    renderWithProviders(withTenant(<ReportSettingsPage />, "tenant_admin"));

    // Wait for form to load
    const titleInput = await screen.findByTestId("field-title");

    // Edit the title field
    await userEvent.clear(titleInput);
    await userEvent.type(titleInput, "Updated");

    // Click save
    await userEvent.click(screen.getByTestId("btn-save"));

    // Wait for PUT to be captured
    await waitFor(() => expect(capturedBodies.length).toBeGreaterThan(0));

    expect(capturedBodies[0]).toMatchObject({
      title: "Updated",
      owner: "NOC",
      timezone: "UTC",
    });
  });

  it("shows Remove logo button when has_logo is true", async () => {
    const settingsWithLogo = { ...defaultSettings, has_logo: true, logo_mime: "image/png" };

    server.use(
      http.get(SETTINGS_URL, () => HttpResponse.json(settingsWithLogo)),
      // The page requests the logo preview when has_logo is true
      http.get(LOGO_URL, () => new HttpResponse(new Uint8Array([0x89, 0x50, 0x4e, 0x47]), { status: 200, headers: { "Content-Type": "image/png" } })),
    );

    renderWithProviders(withTenant(<ReportSettingsPage />, "tenant_admin"));

    expect(await screen.findByTestId("btn-remove-logo")).toBeInTheDocument();
  });

  it("logo upload calls the multipart fetch endpoint", async () => {
    const logoFetchCalled = vi.fn();

    server.use(
      http.get(SETTINGS_URL, () => HttpResponse.json(defaultSettings)),
      http.put(LOGO_URL, () => {
        logoFetchCalled();
        return HttpResponse.json({ ...defaultSettings, has_logo: true, logo_mime: "image/png" });
      }),
    );

    renderWithProviders(withTenant(<ReportSettingsPage />, "tenant_admin"));

    // Wait for form to load
    await screen.findByTestId("btn-save");

    // Mantine FileInput renders a hidden <input type="file"> as a sibling before the wrapper root.
    // The visible button gets data-testid; the actual file input is found by type="file" in the container.
    const pngBytes = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
    const file = new File([pngBytes], "logo.png", { type: "image/png" });

    // Find ANY hidden file input in the document (there is only one)
    const actualInput = document.querySelector('input[type="file"]') as HTMLInputElement | null;
    expect(actualInput).not.toBeNull();

    Object.defineProperty(actualInput, "files", {
      value: [file],
      configurable: true,
    });
    fireEvent.change(actualInput!);

    // Wait for state update and click Upload
    const uploadBtn = screen.getByTestId("btn-upload");
    await waitFor(() => expect(uploadBtn).not.toBeDisabled());
    await userEvent.click(uploadBtn);

    await waitFor(() => expect(logoFetchCalled).toHaveBeenCalled());
  });

  it("surfaces a red notification when saving returns 403", async () => {
    const showSpy = vi.spyOn(notifications, "show");
    server.use(
      http.get(SETTINGS_URL, () => HttpResponse.json(defaultSettings)),
      http.put(SETTINGS_URL, () => HttpResponse.json({}, { status: 403 })),
    );

    renderWithProviders(withTenant(<ReportSettingsPage />, "tenant_admin"));

    await userEvent.click(await screen.findByTestId("btn-save"));

    await waitFor(() =>
      expect(showSpy).toHaveBeenCalledWith(
        expect.objectContaining({ color: "red" }),
      ),
    );
    // the page does not crash; the form is still present
    expect(screen.getByTestId("btn-save")).toBeInTheDocument();
    showSpy.mockRestore();
  });
});

describe("ReportSettingsPage — non-admin roles", () => {
  it("shows the admins-only alert for read_only role (no form)", async () => {
    // No settings handler needed — the form won't render for non-admin
    renderWithProviders(withTenant(<ReportSettingsPage />, "read_only"));

    expect(await screen.findByTestId("admins-only-alert")).toBeInTheDocument();
    expect(screen.queryByTestId("btn-save")).not.toBeInTheDocument();
  });

  it("shows the admins-only alert for operator role (no form)", async () => {
    renderWithProviders(withTenant(<ReportSettingsPage />, "operator"));

    expect(await screen.findByTestId("admins-only-alert")).toBeInTheDocument();
    expect(screen.queryByTestId("btn-save")).not.toBeInTheDocument();
  });
});
