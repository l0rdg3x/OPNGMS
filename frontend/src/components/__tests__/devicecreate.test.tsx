import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { DeviceCreateModal } from "../DeviceCreateModal";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

describe("DeviceCreateModal", () => {
  it("submits onboarding and closes on success", async () => {
    const onClose = vi.fn();
    let posted: { name?: string; base_url?: string } | null = null;
    server.use(
      http.post("/api/tenants/t1/devices", async ({ request }) => {
        posted = (await request.json()) as { name?: string; base_url?: string };
        return HttpResponse.json({ id: "d1", name: posted.name, status: "reachable" }, { status: 201 });
      }),
      http.get("/api/tenants/t1/devices", () => HttpResponse.json([])),
    );
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={onClose} />);
    await userEvent.type(screen.getByLabelText(/nome/i), "fw1");
    await userEvent.type(screen.getByLabelText(/url/i), "https://fw1");
    await userEvent.type(screen.getByLabelText(/api key/i), "k");
    await userEvent.type(screen.getByLabelText(/api secret/i), "s");
    await userEvent.click(screen.getByRole("button", { name: /salva/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(posted?.base_url).toBe("https://fw1");
  });
});
