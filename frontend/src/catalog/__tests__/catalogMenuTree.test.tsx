// frontend/src/catalog/__tests__/catalogMenuTree.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogMenuTree } from "../CatalogMenuTree";
import type { MenuNode } from "../catalogTypes";

const MENU: MenuNode[] = [
  { id: "Services", label: "Services", order: 50, children: [
    { id: "Unbound", label: "Unbound DNS", order: 0, children: [
      { id: "General", label: "General", order: 10, url: "/ui/unbound/general", model_id: "unbound" },
      { id: "Stats", label: "Statistics", order: 90, url: "/ui/diagnostics/x", model_id: null },
    ]},
  ]},
];

describe("CatalogMenuTree", () => {
  it("selects a mapped leaf's model", () => {
    const onSelect = vi.fn();
    renderWithProviders(
      <CatalogMenuTree nodes={MENU} baseUrl="https://1.2.3.4" search="" selected={null} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("General"));
    expect(onSelect).toHaveBeenCalledWith("unbound");
  });

  it("renders an unmapped leaf as a WebGUI deep-link", () => {
    renderWithProviders(
      <CatalogMenuTree nodes={MENU} baseUrl="https://1.2.3.4" search="" selected={null} onSelect={() => {}} />);
    const link = screen.getByTestId("catalog-menu-link-Stats");
    expect(link).toHaveAttribute("href", "https://1.2.3.4/ui/diagnostics/x");
  });

  it("filters by search (hides non-matching leaves)", () => {
    renderWithProviders(
      <CatalogMenuTree nodes={MENU} baseUrl="https://1.2.3.4" search="statistics" selected={null} onSelect={() => {}} />);
    expect(screen.queryByText("General")).toBeNull();
    expect(screen.getByText("Statistics")).toBeInTheDocument();
  });
});
