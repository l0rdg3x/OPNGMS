import { fireEvent, screen, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { ProposeAliasModal } from "../ProposeAliasModal";
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

describe("ProposeAliasModal", () => {
  it("renders the form fields when opened", () => {
    renderWithProviders(
      withTenant(
        <ProposeAliasModal deviceId="d1" opened={true} onClose={() => {}} />,
      ),
    );

    expect(screen.getByText(/propose alias change/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/name/i)).toBeInTheDocument();
    // Use getByRole for the Select combobox to avoid matching the dropdown listbox
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByLabelText(/content/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /create/i })).toBeInTheDocument();
  });

  it("does not render when closed", () => {
    renderWithProviders(
      withTenant(
        <ProposeAliasModal deviceId="d1" opened={false} onClose={() => {}} />,
      ),
    );

    expect(screen.queryByText(/propose alias change/i)).not.toBeInTheDocument();
  });

  it("submits the correct payload and calls onClose on success", async () => {
    const onClose = vi.fn();
    let capturedBody: unknown;

    server.use(
      http.post(CHANGES_URL, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(
          {
            id: "c1",
            device_id: "d1",
            kind: "alias",
            operation: "set",
            target: "web_servers",
            status: "draft",
            scheduled_at: null,
            applied_at: null,
            created_at: "2026-06-10T10:00:00Z",
          },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(
      withTenant(
        <ProposeAliasModal deviceId="d1" opened={true} onClose={onClose} />,
      ),
    );

    // Fill in the name
    const nameInput = screen.getByLabelText(/name/i);
    fireEvent.change(nameInput, { target: { value: "web_servers" } });

    // Fill in content (two lines)
    const contentTextarea = screen.getByLabelText(/content/i);
    fireEvent.change(contentTextarea, { target: { value: "10.0.0.1\n10.0.0.2" } });

    // Submit the form (default operation is "set", default type is "host")
    const submitButton = screen.getByRole("button", { name: /create/i });
    fireEvent.click(submitButton);

    await waitFor(() => {
      expect(capturedBody).toEqual({
        kind: "alias",
        operation: "set",
        target: "web_servers",
        payload: {
          name: "web_servers",
          type: "host",
          content: ["10.0.0.1", "10.0.0.2"],
        },
      });
    });

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });

  it("does not call onClose when the server returns an error", async () => {
    const onClose = vi.fn();

    server.use(
      http.post(CHANGES_URL, () => {
        return HttpResponse.json({ detail: "forbidden" }, { status: 403 });
      }),
    );

    renderWithProviders(
      withTenant(
        <ProposeAliasModal deviceId="d1" opened={true} onClose={onClose} />,
      ),
    );

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "blocked_alias" },
    });

    fireEvent.click(screen.getByRole("button", { name: /create/i }));

    // The button loading state transitions: pending → idle (after rejection)
    // Wait for the mutation to settle — create button is no longer loading
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /create/i })).not.toBeDisabled();
    });

    expect(onClose).not.toHaveBeenCalled();
  });

  it("trims and filters blank lines from content", async () => {
    const onClose = vi.fn();
    let capturedBody: unknown;

    server.use(
      http.post(CHANGES_URL, async ({ request }) => {
        capturedBody = await request.json();
        return HttpResponse.json(
          {
            id: "c2",
            device_id: "d1",
            kind: "alias",
            operation: "set",
            target: "db_servers",
            status: "draft",
            scheduled_at: null,
            applied_at: null,
            created_at: "2026-06-10T10:00:00Z",
          },
          { status: 201 },
        );
      }),
    );

    renderWithProviders(
      withTenant(
        <ProposeAliasModal deviceId="d1" opened={true} onClose={onClose} />,
      ),
    );

    fireEvent.change(screen.getByLabelText(/name/i), {
      target: { value: "db_servers" },
    });

    // Content with blank lines and whitespace that should be filtered
    fireEvent.change(screen.getByLabelText(/content/i), {
      target: { value: "  192.168.1.1  \n\n192.168.1.2\n   " },
    });

    fireEvent.click(screen.getByRole("button", { name: /create/i }));

    await waitFor(() => {
      expect(capturedBody).toEqual({
        kind: "alias",
        operation: "set",
        target: "db_servers",
        payload: {
          name: "db_servers",
          type: "host",
          content: ["192.168.1.1", "192.168.1.2"],
        },
      });
    });

    await waitFor(() => {
      expect(onClose).toHaveBeenCalled();
    });
  });
});
