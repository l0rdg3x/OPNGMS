// frontend/src/catalog/__tests__/catalogDiff.test.tsx
import { describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogModelForm } from "../CatalogModelForm";
import { CatalogMenuTree } from "../CatalogMenuTree";
import type { CatalogDiff, CatalogModelLive, MenuNode } from "../catalogTypes";

const LIVE: CatalogModelLive = {
  model: {
    id: "unbound", title: "Unbound", model_root: "unbound", endpoints: {},
    fields: [
      { path: "general.enabled", type: "bool" },
      { path: "general.port", type: "int" },
    ],
    grids: [],
    pages: [{ id: "general", fields: ["general.enabled", "general.port"] }],
  },
  values: { "general.enabled": "0", "general.port": "53" },
  grids: {}, field_options: {}, grid_field_options: {}, reachable: true, read_only: false,
};

const DIFF: CatalogDiff = {
  from: "26.1.8",
  to: "26.1.9",
  available_baselines: ["26.1.8"],
  diff: {
    added_models: ["unbound"],
    removed_models: [],
    models: {
      unbound: {
        added_fields: ["general.enabled"],
        removed_fields: [],
        changed_fields: ["general.port"],
      },
    },
  },
};

describe("diff badges", () => {
  it("renders 'New since' on an added field and 'Changed since' on a changed field", () => {
    renderWithProviders(
      <CatalogModelForm live={LIVE} onPropose={vi.fn()} diff={DIFF} diffFrom="26.1.8" />,
    );
    expect(screen.getByText(/New since 26\.1\.8/)).toBeInTheDocument();
    expect(screen.getByText(/Changed since 26\.1\.8/)).toBeInTheDocument();
  });

  it("renders no badges when no diff is provided", () => {
    renderWithProviders(<CatalogModelForm live={LIVE} onPropose={vi.fn()} />);
    expect(screen.queryByText(/New since/)).toBeNull();
    expect(screen.queryByText(/Changed since/)).toBeNull();
  });
});

const MENU: MenuNode[] = [
  { id: "Services", label: "Services", order: 50, children: [
    { id: "Unbound", label: "Unbound DNS", order: 0, children: [
      { id: "General", label: "General", order: 10, url: "/ui/unbound/general", model_id: "unbound" },
      { id: "Stats", label: "Statistics", order: 90, url: "/ui/diagnostics/x", model_id: "diagnostics" },
    ]},
  ]},
];

describe("menu diff dot", () => {
  it("shows a changes badge next to a model with diff entries", () => {
    renderWithProviders(
      <CatalogMenuTree
        nodes={MENU} baseUrl="https://1.2.3.4" search="general" selected={null}
        onSelect={vi.fn()} diff={DIFF.diff} />,
    );
    // "unbound" model is in added_models / has diff.models entries → dot/count visible.
    expect(screen.getByTestId("catalog-menu-diff-unbound")).toBeInTheDocument();
  });
});
