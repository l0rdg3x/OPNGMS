import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { DeviceCreateModal } from "../DeviceCreateModal";
import { server } from "../../test/server";
import { renderWithProviders } from "../../test/utils";

interface Posted {
  name?: string;
  base_url?: string;
  verify_tls?: boolean;
  tls_fingerprint?: string;
}

function mockCreate() {
  let posted: Posted | null = null;
  server.use(
    http.post("/api/tenants/t1/devices", async ({ request }) => {
      posted = (await request.json()) as Posted;
      return HttpResponse.json({ id: "d1", name: posted.name, status: "reachable" }, { status: 201 });
    }),
    http.get("/api/tenants/t1/devices", () => HttpResponse.json([])),
  );
  return () => posted;
}

async function fillRequiredFields() {
  await userEvent.type(screen.getByLabelText(/name/i), "fw1");
  await userEvent.type(screen.getByLabelText(/url/i), "https://fw1");
  await userEvent.type(screen.getByLabelText(/api key/i), "k");
  await userEvent.type(screen.getByLabelText(/api secret/i), "s");
}

describe("DeviceCreateModal", () => {
  it("submits onboarding and closes on success", async () => {
    const onClose = vi.fn();
    const getPosted = mockCreate();
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={onClose} />);
    await fillRequiredFields();
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(getPosted()?.base_url).toBe("https://fw1");
  });

  it("hides the TLS-pinning warning and fingerprint field while verification is on", () => {
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={vi.fn()} />);
    expect(screen.queryByTestId("verify-tls-warning")).not.toBeInTheDocument();
    expect(screen.queryByTestId("tls-fingerprint")).not.toBeInTheDocument();
  });

  it("shows the warning and fingerprint field once verification is switched off", async () => {
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={vi.fn()} />);
    await userEvent.click(screen.getByLabelText(/verify tls/i));
    expect(screen.getByTestId("verify-tls-warning")).toBeInTheDocument();
    expect(screen.getByTestId("tls-fingerprint")).toBeInTheDocument();
  });

  it("sends a typed fingerprint in the request body", async () => {
    const onClose = vi.fn();
    const getPosted = mockCreate();
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={onClose} />);
    await fillRequiredFields();
    await userEvent.click(screen.getByLabelText(/verify tls/i));
    await userEvent.type(screen.getByTestId("tls-fingerprint"), "AA:BB:CC");
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(getPosted()?.tls_fingerprint).toBe("AA:BB:CC");
  });

  it("omits a typed fingerprint when verification is switched back on", async () => {
    const onClose = vi.fn();
    const getPosted = mockCreate();
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={onClose} />);
    await fillRequiredFields();
    await userEvent.click(screen.getByLabelText(/verify tls/i));
    await userEvent.type(screen.getByTestId("tls-fingerprint"), "AA:BB:CC");
    await userEvent.click(screen.getByLabelText(/verify tls/i));
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(getPosted()?.verify_tls).toBe(true);
    expect(getPosted()).not.toHaveProperty("tls_fingerprint");
  });

  it("omits tls_fingerprint from the request body when left empty", async () => {
    const onClose = vi.fn();
    const getPosted = mockCreate();
    renderWithProviders(<DeviceCreateModal tenantId="t1" opened onClose={onClose} />);
    await fillRequiredFields();
    await userEvent.click(screen.getByLabelText(/verify tls/i));
    await userEvent.click(screen.getByRole("button", { name: /save/i }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(getPosted()).not.toHaveProperty("tls_fingerprint");
  });
});
