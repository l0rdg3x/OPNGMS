// frontend/src/catalog/__tests__/catalogFieldInput.test.tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import { CatalogFieldInput } from "../CatalogFieldInput";

describe("CatalogFieldInput", () => {
  it("renders a switch for bool and reports 1/0", () => {
    const onChange = vi.fn();
    renderWithProviders(
      <CatalogFieldInput field={{ path: "general.enabled", type: "bool" }} value="0" onChange={onChange} disabled={false} />,
    );
    fireEvent.click(screen.getByTestId("catalog-field-general.enabled"));
    expect(onChange).toHaveBeenCalledWith("general.enabled", "1");
  });

  it("renders a number input for int", () => {
    renderWithProviders(
      <CatalogFieldInput field={{ path: "general.port", type: "int" }} value="53" onChange={() => {}} disabled={false} />,
    );
    expect(screen.getByTestId("catalog-field-general.port")).toHaveValue("53");
  });

  it("renders a select for enum with options", () => {
    renderWithProviders(
      <CatalogFieldInput
        field={{ path: "x", type: "enum", options: ["a", "b"] }}
        value="a" onChange={() => {}} disabled={false} />,
    );
    expect(screen.getByTestId("catalog-field-x")).toBeInTheDocument();
  });
});
