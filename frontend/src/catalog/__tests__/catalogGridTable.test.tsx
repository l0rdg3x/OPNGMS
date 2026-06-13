// frontend/src/catalog/__tests__/catalogGridTable.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogGridTable } from "../CatalogGridTable";
import type { CatalogGrid, GridRow } from "../catalogTypes";

const GRID: CatalogGrid = {
  path: "hosts", endpoints: {},
  fields: [{ path: "hostname", type: "string" }],
};
const ROWS: GridRow[] = [{ uuid: "ab-12", hostname: "web" }];

describe("CatalogGridTable", () => {
  it("renders existing rows and emits a delete op", () => {
    const onOps = vi.fn();
    renderWithProviders(<CatalogGridTable grid={GRID} rows={ROWS} disabled={false} onOps={onOps} />);
    expect(screen.getByText("web")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("catalog-grid-hosts-del-ab-12"));
    expect(onOps).toHaveBeenCalledWith([{ op: "del", grid: "hosts", uuid: "ab-12" }]);
  });
});
