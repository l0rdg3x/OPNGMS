// frontend/src/catalog/__tests__/catalogModelForm.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogModelForm } from "../CatalogModelForm";
import type { CatalogModelLive } from "../catalogTypes";

const LIVE: CatalogModelLive = {
  model: {
    id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
    fields: [{ path: "general.enabled", type: "bool" }, { path: "general.port", type: "int" }],
    grids: [], pages: [{ id: "general", fields: ["general.enabled", "general.port"] }],
  },
  values: { "general.enabled": "0", "general.port": "53" },
  grids: {}, reachable: true, read_only: false,
};

describe("CatalogModelForm", () => {
  it("proposes only changed scalars", async () => {
    const onPropose = vi.fn().mockResolvedValue(undefined);
    renderWithProviders(<CatalogModelForm live={LIVE} onPropose={onPropose} />);
    fireEvent.click(screen.getByTestId("catalog-field-general.enabled")); // 0 -> 1
    fireEvent.click(screen.getByTestId("catalog-propose"));
    await waitFor(() => expect(onPropose).toHaveBeenCalled());
    expect(onPropose).toHaveBeenCalledWith({
      model_id: "unbound", scalars: { "general.enabled": "1" }, grids: [],
    });
  });

  it("disables propose when read_only", () => {
    renderWithProviders(
      <CatalogModelForm live={{ ...LIVE, read_only: true }} onPropose={vi.fn()} />);
    expect(screen.queryByTestId("catalog-propose")).toBeNull();
    expect(screen.getByText(/safety denylist/i)).toBeInTheDocument();
  });

  it("shows the unreachable banner and no propose", () => {
    renderWithProviders(
      <CatalogModelForm live={{ ...LIVE, reachable: false, values: {} }} onPropose={vi.fn()} />);
    expect(screen.getByText(/unreachable/i)).toBeInTheDocument();
    expect(screen.queryByTestId("catalog-propose")).toBeNull();
  });
});
